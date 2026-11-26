"""
VM Manager - Controls VM lifecycle, snapshots, and recovery.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module provides the VMManager class for controlling virtual machines via
libvirt, managing snapshots, and implementing automatic recovery mechanisms.

The low-level libvirt/virsh plumbing (lifecycle, snapshots, screenshot, guest
agent, domain (un)definition) now lives in the shared ``vmctl_core`` backend
(``vmctl_core.backends.LibvirtBackend``). VMManager wraps a single backend
instance and adds the EXHIBITION-ONLY orchestration on top: revert-to-'ready'
start semantics, auto-revert/heartbeat readiness, USB-passthrough provisioning,
and the operator network guard. Dependency arrow: exhibition -> vmctl-core only.
"""

import ipaddress
import logging
import os
import signal
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from vmctl_core.backends import LibvirtBackend, ProcessBackend

logger = logging.getLogger(__name__)

# Hard bounds on virsh shell-outs. Every mutating VM op runs under VMManager._op_lock; WITHOUT a
# timeout a hung libvirtd wedges run() forever, the executor thread never returns, and _op_lock is
# held indefinitely — so every LATER op (auto-recovery, an operator's /vm/stop) deadlocks behind it.
# With a timeout, run() kills the child and raises TimeoutExpired, which unwinds out of the `with
# _op_lock` block and frees the lock. Values are generous for the slow ops (revert/create can carry
# RAM state) but finite.
_VIRSH_QUICK_TIMEOUT_S = 15  # domstate / snapshot-list — near-instant
_VIRSH_OP_TIMEOUT_S = 60  # destroy / start / snapshot-delete
_VIRSH_SLOW_TIMEOUT_S = 120  # snapshot-revert / snapshot-create-as (may persist RAM state)


class VMManager:
    """
    Manages a virtual machine's lifecycle, snapshots, and automatic recovery.

    This class provides:
    - VM start/stop operations via libvirt (virsh)
    - Snapshot management (create, delete, revert)
    - QEMU guest agent responsiveness checks
    - Automatic revert on failure

    Attributes:
        vm_name: Name of the VM in libvirt
        snapshot_name: Name of the "ready" snapshot to revert to
        auto_revert_enabled: Whether automatic revert is enabled
    """

    def __init__(
        self,
        vm_name: str,
        snapshot_name: str = "ready",
        auto_revert_enabled: bool = True,
        on_reset_callback: Optional[Callable] = None,
        startup_wait_interval: float = 10.0,
        startup_max_attempts: int = 30,
        qemu_agent_timeout: float = 5.0,
        uri: str = "qemu:///system",
        backend: Optional[ProcessBackend] = None,
    ):
        """
        Initialize VMManager.

        Args:
            vm_name: Name of the VM in libvirt
            snapshot_name: Name of the reference snapshot (default: "ready")
            auto_revert_enabled: Enable automatic revert on failure
            on_reset_callback: Optional callback function to call on VM reset
            startup_wait_interval: Seconds between VM responsiveness checks at startup
            startup_max_attempts: Max responsiveness checks before giving up
            qemu_agent_timeout: Timeout (s) for QEMU guest agent commands
            uri: libvirt connection URI for the shared backend
            backend: A non-libvirt backend (emulator process). It has no guest on a host
                network, so liveness comes from the host; see ``host_liveness``.
        """
        self.vm_name = vm_name
        self.snapshot_name = snapshot_name
        self.auto_revert_enabled = auto_revert_enabled
        self.on_reset_callback = on_reset_callback
        self.startup_wait_interval = startup_wait_interval
        self.startup_max_attempts = startup_max_attempts
        self.qemu_agent_timeout = qemu_agent_timeout

        # Shared core backend bound to this one domain. All low-level libvirt
        # plumbing (lifecycle, snapshots, screenshot, guest agent, net discovery)
        # lives here; VMManager keeps the exhibition orchestration on top.
        self.backend: LibvirtBackend | ProcessBackend = backend or LibvirtBackend(vm_name, uri=uri)
        # True when nothing runs inside the machine that could post a heartbeat or reach the API.
        self.host_liveness = backend is not None
        self.probe_detail: Optional[dict] = None
        # The orchestration steps that still shell out directly (the staged golden-snapshot
        # refresh, USB/XML plumbing) MUST talk to the same libvirt the backend uses. Without
        # the flag `virsh` falls back to the caller's default URI — qemu:///session for a
        # non-root service user — where the domain does not exist: "failed to get domain".
        # Everything delegated to the backend already carries `-c <uri>`.
        self._virsh_conn = ["virsh", "-c", uri]

        # Serializes all VM lifecycle mutations (start/stop/restart/snapshot)
        # so an auto-recovery and a manual request can never issue overlapping
        # virsh reverts. Reentrant: restart_vm() calls start_vm() while held.
        self._op_lock = threading.RLock()

        # Discover VM network subnet from libvirt (via the shared backend)
        self.vm_network = self._discover_vm_network()
        # monotonic stamp of the last failed discovery; see ensure_vm_network()
        self._network_miss_at = 0.0

        logger.info(
            f"Initializing VM Manager for VM '{vm_name}' with snapshot '{snapshot_name}'"
            f" on network {self.vm_network or 'unknown'}"
        )

        # Check if snapshot exists
        if not self.snapshot_exists():
            logger.warning(
                f"Snapshot '{snapshot_name}' does not exist. "
                f"VM control will be limited until snapshot is created."
            )

    def _discover_vm_network(self) -> Optional[str]:
        """Discover the VM's network subnet from libvirt (via the shared backend).
        Returns CIDR notation (e.g. '192.168.122.0/24') or None."""
        try:
            return self.backend.discover_vm_network()
        except Exception as e:
            logger.warning(f"Could not discover VM network: {e}")
            return None

    def ensure_vm_network(self) -> Optional[str]:
        """Return the VM subnet, RE-DISCOVERING it if the one-shot probe at init came back empty
        (a transient libvirt hiccup at startup otherwise leaves ``vm_network`` None for the whole
        process — which makes the guest-origin guard fail open forever). Caches on success.
        A miss is cached for a few seconds: the guards call this on the event loop for every
        guest request, and each re-probe is two blocking virsh calls."""
        if not self.vm_network and time.monotonic() - self._network_miss_at > 5.0:
            self.vm_network = self._discover_vm_network()
            if not self.vm_network:
                self._network_miss_at = time.monotonic()
        return self.vm_network

    def is_from_vm(self, client_ip: str) -> bool:
        """Check if a client IP belongs to the VM's network. Re-discovers the subnet on demand so a
        startup discovery miss doesn't permanently blind the check."""
        network = self.ensure_vm_network()
        if not network:
            return False
        try:
            return ipaddress.IPv4Address(client_ip) in ipaddress.IPv4Network(network)
        except ValueError:
            return False

    def snapshot_exists(self) -> bool:
        """Check if the configured snapshot exists."""
        try:
            snapshots = self.list_snapshots()
            exists = self.snapshot_name in snapshots
            if exists:
                logger.debug(f"Snapshot '{self.snapshot_name}' exists")
            else:
                logger.debug(f"Snapshot '{self.snapshot_name}' does not exist")
            return exists
        except Exception as e:
            logger.error(f"Error checking snapshot existence: {e}")
            return False

    def list_snapshots(self) -> List[str]:
        """
        List all snapshots for the VM.

        Returns:
            List of snapshot names

        Raises:
            BackendError: If the underlying virsh command fails
        """
        logger.debug(f"Listing snapshots for VM '{self.vm_name}'")
        snapshots = self.backend.snapshot_list()
        logger.debug(f"Found {len(snapshots)} snapshots: {snapshots}")
        return snapshots

    def _delete_snapshot_quiet(self, name: str, children: bool = False) -> None:
        """Best-effort snapshot delete. Swallows a missing-snapshot error, logs anything else.

        Used for cleanup paths where a failure to delete must not itself abort the caller.
        """
        argv = [*self._virsh_conn, "snapshot-delete", self.vm_name, name]
        if children:
            argv.append("--children")
        try:
            subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=True,
                timeout=_VIRSH_OP_TIMEOUT_S,
            )
            logger.debug(f"Deleted snapshot '{name}'")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr or ""
            if (
                "No snapshot with name" not in stderr
                and "domain snapshot not found" not in stderr.lower()
            ):
                logger.warning(f"Could not delete snapshot '{name}': {stderr}")
        except subprocess.TimeoutExpired:
            logger.warning(f"Timed out deleting snapshot '{name}'")

    def create_snapshot(self, snapshot_name: Optional[str] = None) -> None:
        """
        Create/refresh the golden snapshot WITHOUT ever leaving the VM with no ready-state
        snapshot to fall back on.

        The naive "delete then create" is unsafe: if the create times out or fails (disk full,
        libvirt down), the golden ``ready`` snapshot is gone permanently and the exhibit can no
        longer self-recover. Instead we:

          1. Snapshot the current state under a STAGING name first. If this fails, the existing
             golden snapshot is untouched — nothing was destroyed.
          2. Verify the staging snapshot actually exists.
          3. Only THEN retire the old golden and create the final one. If step 3's create fails,
             the staging snapshot is deliberately left in place as a recovery point (an operator
             can revert to it), so there is never a moment with zero ready-state snapshots.

        Args:
            snapshot_name: Name for the snapshot (default: use self.snapshot_name)

        Raises:
            subprocess.CalledProcessError: If snapshot creation fails
            subprocess.TimeoutExpired: If a virsh op times out
            RuntimeError: If the staging snapshot cannot be verified after creation
        """
        name = snapshot_name or self.snapshot_name
        if self.host_liveness:
            # the backend replaces a snapshot atomically; the steps below are virsh-specific
            with self._op_lock:
                self.backend.snapshot_create(name)
            logger.info(f"Snapshot '{name}' created successfully")
            return
        staging = f"{name}__staging"
        logger.info(f"Creating snapshot '{name}' for VM '{self.vm_name}'")

        # NEVER pass children=True anywhere in this sequence. libvirt makes each new snapshot a
        # CHILD of the current one, so the golden created in step 3 is a child of `staging` —
        # and deleting a parent with --children takes the whole subtree with it, i.e. exactly
        # the snapshot each step is protecting. Deleting a parent WITHOUT --children re-parents
        # the child and keeps it (verified against libvirt 11.3 on the lab host). With
        # --children, the very first create_snapshot for a VM (no golden yet) ended with NO
        # snapshot at all: step 4 deleted `staging` and the fresh golden hanging off it, and the
        # exhibit then refused to start for lack of a recovery baseline. A refresh (golden
        # already present) survived by luck of ordering, which is why this never showed in
        # production, and the mocked tests track a flat set with no parentage at all.
        with self._op_lock:
            # 1. Clear any staging snapshot left behind by a previously aborted run, then create
            #    the new snapshot under the staging name. The golden '{name}' is still untouched
            #    here, so a failure at this point is fully recoverable.
            self._delete_snapshot_quiet(staging)
            subprocess.run(
                [*self._virsh_conn, "snapshot-create-as", self.vm_name, staging],
                capture_output=True,
                text=True,
                check=True,
                timeout=_VIRSH_SLOW_TIMEOUT_S,
            )

            # 2. Verify the staging snapshot is really there before touching the golden one.
            if staging not in self.list_snapshots():
                self._delete_snapshot_quiet(staging)
                raise RuntimeError(
                    f"Staging snapshot '{staging}' not found after creation - refusing to "
                    f"delete the existing '{name}' snapshot"
                )

            # 3. Retire the old golden and promote the verified state into its place. If the
            #    final create fails, LEAVE the staging snapshot as a recovery point rather than
            #    cleaning it up — better a mis-named snapshot than none at all.
            self._delete_snapshot_quiet(name)
            try:
                subprocess.run(
                    [*self._virsh_conn, "snapshot-create-as", self.vm_name, name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=_VIRSH_SLOW_TIMEOUT_S,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.critical(
                    f"Failed to create final snapshot '{name}'; the verified snapshot '{staging}' "
                    f"has been kept as a recovery point - revert to it or retry the snapshot"
                )
                raise

            # 4. Success — the golden snapshot is committed; the staging copy is now redundant.
            #    Without --children (see above) libvirt re-parents the golden and keeps it.
            self._delete_snapshot_quiet(staging)
        logger.info(f"Snapshot '{name}' created successfully")

    def delete_snapshot(self, snapshot_name: Optional[str] = None) -> None:
        """
        Delete a snapshot.

        Args:
            snapshot_name: Name of snapshot to delete (default: use self.snapshot_name)

        Raises:
            BackendError: If deletion fails
        """
        name = snapshot_name or self.snapshot_name
        logger.info(f"Deleting snapshot '{name}' for VM '{self.vm_name}'")

        with self._op_lock:
            self.backend.snapshot_delete(name)
        logger.info(f"Snapshot '{name}' deleted successfully")

    def stop_vm(self) -> None:
        """
        Stop (destroy) the VM.

        This is a hard stop, equivalent to pulling the power cord.
        The VM will be forcefully terminated.

        Raises:
            BackendError: If stop fails (excluding "not running")
        """
        logger.info(f"Stopping VM '{self.vm_name}'")

        with self._op_lock:
            self.backend.stop()  # idempotent; tolerates "not running"
            logger.info("VM stopped successfully")

    def start_vm(self) -> None:
        """
        Start the VM by reverting to the ready snapshot.

        Reverts to the configured snapshot and guarantees the VM is RUNNING afterwards —
        a snapshot captured while the VM was off restores it off, so that case is booted
        explicitly rather than reported as started.

        Raises:
            BackendError: If revert/start fails
        """
        with self._op_lock:
            # Check if snapshot exists
            if self.snapshot_exists():
                logger.info(
                    f"Starting VM '{self.vm_name}' by reverting to snapshot '{self.snapshot_name}'"
                )

                # Call reset callback if provided
                if self.on_reset_callback:
                    try:
                        self.on_reset_callback()
                    except Exception as e:
                        logger.error(f"Error in reset callback: {e}")

                # Revert to snapshot. The backend flushes conntrack after the revert to clear
                # stale TCP NAT state.
                self.backend.snapshot_revert(self.snapshot_name)

                # A revert restores the state the snapshot CAPTURED: taken on a running VM it
                # comes back running, taken while the VM was off it comes back off. That is
                # libvirt-correct and silent — the old log line claimed "started" either way,
                # so an exhibit could sit dark behind a success message until the heartbeat
                # noticed. The caller asked for a start, so start it.
                if self.backend.is_running():
                    logger.info("VM reverted to snapshot and started successfully")
                else:
                    logger.info(
                        f"Snapshot '{self.snapshot_name}' was taken with the VM off — "
                        f"reverted, now booting"
                    )
                    self.backend.start()
            else:
                # FAIL CLOSED: with no 'ready' snapshot there is no clean recovery baseline to
                # revert to. Booting the raw disk here would put the *mutable / possibly tampered*
                # exhibit state on screen and — worse — let the auto-recovery path "recover" into
                # un-reverted state. Refuse instead of silently booting dirty state; an operator
                # must (re-)create the ready snapshot before the VM can be started.
                logger.error(
                    f"Snapshot '{self.snapshot_name}' does not exist - refusing to start VM "
                    f"without a recovery baseline (fail-closed)"
                )
                raise RuntimeError(
                    f"Snapshot '{self.snapshot_name}' does not exist for VM '{self.vm_name}'; "
                    f"refusing to boot un-reverted disk state. Create the ready snapshot first."
                )

            # Exhibition's sudo-backed conntrack flush on top of the backend's
            # (see _flush_conntrack) — guarantees the kiosk service user clears
            # stale TCP NAT state even without conntrack group membership.
            self._flush_conntrack()

    def _flush_conntrack(self) -> None:
        """Flush conntrack entries for the VM network to clear stale TCP state.

        After a snapshot revert the VM resumes with TCP connections from before
        the snapshot. The kernel's conntrack table still has the old entries,
        causing packets with mismatched sequence numbers to be dropped.

        Exhibition runs this via ``sudo`` (see deployment/sudoers.d) because the
        unattended kiosk service user is not in a conntrack-capable group; the
        shared backend's plain-``conntrack`` flush would silently no-op there.
        The backend ALSO flushes on its own start/revert (plain conntrack); this
        sudo flush is the exhibition-deployment guarantee on top.
        """
        if self.host_liveness:
            return  # no VM network
        try:
            subnet = self.vm_network or "192.168.122.0/24"
            subprocess.run(
                ["sudo", "conntrack", "-D", "-s", subnet],
                capture_output=True,
                text=True,
                timeout=5,
            )
            logger.info("Flushed conntrack entries for VM network")
        except FileNotFoundError:
            logger.debug("conntrack command not found, skipping flush")
        except Exception as e:
            logger.debug(f"conntrack flush: {e}")

    def check_vm_responsiveness(self, timeout: Optional[float] = None) -> Optional[bool]:
        """
        Check responsiveness using the backend's guest agent or process probe.

        This sends a guest-ping command to the QEMU guest agent running
        inside the VM. Requires qemu-guest-agent to be installed and running
        in the guest. Process backends instead inspect host-side liveness.

        Args:
            timeout: Timeout in seconds for the check (default: qemu_agent_timeout)

        Returns:
            True if responsive, False if unhealthy, None while a process probe warms up.
        """
        if timeout is None:
            timeout = self.qemu_agent_timeout
        logger.debug(f"Checking VM '{self.vm_name}' responsiveness via backend")
        responsive = self.backend.guest_ping(timeout=timeout)
        if isinstance(self.backend, ProcessBackend):
            self.probe_detail = self.backend.guest_tasks() or None
            # guest_ping is boolean; only the probe detail distinguishes warm-up from failure.
            if self.probe_detail and self.probe_detail.get("alive", False) is None:
                return None
        logger.debug(f"VM responsive: {responsive}")
        return responsive

    def wait_for_vm_ready(
        self, check_interval: Optional[float] = None, max_attempts: Optional[int] = None
    ) -> bool:
        """
        Wait for VM to become responsive after start/revert.

        Polls the VM using QEMU guest agent until it responds or timeout.

        Args:
            check_interval: Seconds between checks (default: startup_wait_interval)
            max_attempts: Maximum attempts before giving up (default: startup_max_attempts)

        Returns:
            True if VM became responsive, False if timed out
        """
        if check_interval is None:
            check_interval = self.startup_wait_interval
        if max_attempts is None:
            max_attempts = self.startup_max_attempts
        logger.info(f"Waiting for VM '{self.vm_name}' to become responsive...")

        for attempt in range(1, max_attempts + 1):
            if self.check_vm_responsiveness():
                logger.info(
                    f"VM is responsive after {attempt} attempts "
                    f"({attempt * check_interval:.0f} seconds)"
                )
                return True

            if attempt < max_attempts:
                logger.debug(
                    f"VM not ready yet (attempt {attempt}/{max_attempts}), "
                    f"waiting {check_interval}s..."
                )
                time.sleep(check_interval)

        logger.warning(
            f"VM did not become responsive after {max_attempts} attempts "
            f"({max_attempts * check_interval:.0f} seconds)"
        )
        return False

    def restart_vm(self, wait_for_ready: bool = True) -> bool:
        """
        Restart the VM by reverting to snapshot.

        Args:
            wait_for_ready: Whether to wait for VM to become responsive

        Returns:
            True if restart successful (and VM responsive if wait_for_ready=True)

        Raises:
            subprocess.CalledProcessError: If restart fails
        """
        logger.info(f"Restarting VM '{self.vm_name}'")

        # Hold the lock across both the revert and the readiness wait so a
        # second restart cannot revert again while this one is still booting.
        with self._op_lock:
            self.start_vm()

            if wait_for_ready:
                return self.wait_for_vm_ready()

            return True

    def get_vm_state(self) -> str:
        """
        Get current VM state from libvirt (via the shared backend).

        Returns:
            VM state string (e.g., "running", "shut off", "paused")
        """
        state = self.backend.state()
        logger.debug(f"VM '{self.vm_name}' state: {state}")
        return state

    def is_running(self) -> bool:
        """
        Check if VM is currently running.

        Returns:
            True if VM is running, False otherwise
        """
        return self.backend.is_running()

    def enable_auto_revert(self) -> None:
        """Enable automatic revert on failure."""
        logger.info("Enabling automatic revert")
        self.auto_revert_enabled = True

    def disable_auto_revert(self) -> None:
        """Disable automatic revert (for maintenance)."""
        logger.info("Disabling automatic revert")
        self.auto_revert_enabled = False

    # ----- USB device provisioning ----------------------------------------
    #
    # The operator never edits domain XML. The controller enumerates host USB
    # devices, generates <hostdev> blocks from a declared vendor:product list,
    # and (via the API orchestration) bakes them into the 'ready' snapshot so
    # they survive the auto-revert. All edits target the *persistent* domain
    # config and back up the original definition first.

    def list_host_usb_devices(self) -> List[dict]:
        """Enumerate host USB devices for the operator's device picker.

        Uses libvirt node-device enumeration (same data as lsusb, but already
        structured with vendor/product names). Returns a list of dicts with
        vendor/product ids + human-readable names.
        """
        result = subprocess.run(
            [*self._virsh_conn, "nodedev-list", "--cap", "usb_device"],
            capture_output=True,
            text=True,
            check=True,
        )
        devices = []
        for dev_name in (line.strip() for line in result.stdout.splitlines() if line.strip()):
            try:
                xml_out = subprocess.run(
                    [*self._virsh_conn, "nodedev-dumpxml", dev_name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                ).stdout
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            try:
                cap = ET.fromstring(xml_out).find(".//capability[@type='usb_device']")
            except ET.ParseError:
                continue
            if cap is None:
                continue
            vendor_el = cap.find("vendor")
            product_el = cap.find("product")
            if vendor_el is None or product_el is None:
                continue
            # libvirt reports ids as '0x045e'; normalise to bare 4-hex.
            vendor = (vendor_el.get("id") or "").lower().removeprefix("0x")
            product = (product_el.get("id") or "").lower().removeprefix("0x")
            if not vendor or not product:
                continue
            # Skip root hubs (vendor 1d6b) — never useful to pass through.
            if vendor == "1d6b":
                continue
            devices.append(
                {
                    "vendor": vendor,
                    "product": product,
                    "vendor_name": (vendor_el.text or "").strip(),
                    "product_name": (product_el.text or "").strip(),
                    "bus": cap.findtext("bus"),
                    "device": cap.findtext("device"),
                }
            )
        return devices

    def _get_persistent_xml(self) -> str:
        """Return the persistent (inactive) domain definition XML."""
        return subprocess.run(
            [*self._virsh_conn, "dumpxml", "--inactive", self.vm_name],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def get_attached_usb_devices(self) -> List[dict]:
        """List USB <hostdev> devices in the persistent domain definition."""
        root = ET.fromstring(self._get_persistent_xml())
        attached = []
        for hd in root.findall(".//devices/hostdev[@type='usb']"):
            src = hd.find("source")
            if src is None:
                continue
            vendor_el = src.find("vendor")
            product_el = src.find("product")
            if vendor_el is None or product_el is None:
                continue
            attached.append(
                {
                    "vendor": (vendor_el.get("id") or "").lower().removeprefix("0x"),
                    "product": (product_el.get("id") or "").lower().removeprefix("0x"),
                    "startup_policy": src.get("startupPolicy", "optional"),
                }
            )
        return attached

    def apply_usb_devices(self, devices: List[dict], backup_dir: Path) -> Path:
        """Author the persistent domain definition's USB passthrough set.

        The controller OWNS USB hostdevs: every existing <hostdev type='usb'> is
        removed and replaced by one per entry in `devices` (idempotent — re-runs
        converge). The original definition is backed up first, the candidate is
        schema-validated, then redefined with `virsh define`.

        Args:
            devices: list of dicts with 'vendor', 'product', and optional
                'startup_policy' (default 'optional').
            backup_dir: directory to write the timestamped XML backup into.

        Returns:
            Path to the backup of the pre-change definition.

        Raises:
            subprocess.CalledProcessError: if validation or define fails.
        """
        with self._op_lock:
            current_xml = self._get_persistent_xml()

            # Back up the pre-change definition (one-command rollback).
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = backup_dir / f"{self.vm_name}-{ts}.xml"
            backup_path.write_text(current_xml)
            logger.info(f"Backed up domain definition to {backup_path}")

            root = ET.fromstring(current_xml)
            devices_el = root.find("devices")
            if devices_el is None:
                raise RuntimeError("Domain XML has no <devices> section")

            # Remove all controller-owned (USB) hostdevs, then re-add from config.
            for hd in devices_el.findall("hostdev[@type='usb']"):
                devices_el.remove(hd)

            for dev in devices:
                vendor = str(dev["vendor"]).lower().removeprefix("0x")
                product = str(dev["product"]).lower().removeprefix("0x")
                policy = dev.get("startup_policy", "optional")
                hd = ET.SubElement(
                    devices_el,
                    "hostdev",
                    {
                        "mode": "subsystem",
                        "type": "usb",
                        "managed": "yes",
                    },
                )
                src = ET.SubElement(hd, "source", {"startupPolicy": policy})
                ET.SubElement(src, "vendor", {"id": f"0x{vendor}"})
                ET.SubElement(src, "product", {"id": f"0x{product}"})

            candidate_xml = ET.tostring(root, encoding="unicode")

            # Write candidate to a temp file for validation + define.
            fd, tmp_path = tempfile.mkstemp(suffix=".xml", prefix="vmdef_")
            os.close(fd)
            try:
                Path(tmp_path).write_text(candidate_xml)

                # Schema-validate before defining (best effort — skip if tool absent).
                try:
                    subprocess.run(
                        ["virt-xml-validate", tmp_path],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=10,
                    )
                except FileNotFoundError:
                    logger.warning(
                        "virt-xml-validate not found; relying on define's own validation"
                    )

                subprocess.run(
                    [*self._virsh_conn, "define", tmp_path],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                logger.info(f"Redefined domain '{self.vm_name}' with {len(devices)} USB device(s)")
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            return backup_path

    def boot_from_persistent_config(self) -> None:
        """Cold-boot the VM from its persistent definition (NOT a snapshot revert).

        Used after redefining the domain so the new hardware is present in the
        running instance, ready to be captured into a fresh 'ready' snapshot.
        """
        with self._op_lock:
            # Hard power-off (idempotent) then cold-boot from the persistent def.
            self.backend.stop()
            self.backend.start()  # backend flushes conntrack on start
            self._flush_conntrack()  # exhibition sudo flush on top
            logger.info(f"Booted '{self.vm_name}' from persistent config")

    def screenshot(self, screen: int = 0) -> bytes:
        """Capture a single screenshot of a VM display head as PNG bytes.

        Delegates to the shared backend's ``screenshot`` (``virsh screenshot
        --screen N``). Head 0 is the primary display; multi-head guests expose
        1, 2, ... in order. Returns the raw PNG bytes for the operator UI.

        Args:
            screen: Display head index to capture (default 0 = primary).

        Raises:
            BackendError: If the screenshot command fails.
        """
        return self.backend.screenshot(screen)


class VideoRecorder:
    """Records the VM display (and, optionally, host-side audio) to an MP4.

    Design rationale (see findings report):
      * The host has no `spicesrc` GStreamer plugin and ffmpeg has no VNC
        demuxer, so neither the SPICE-client nor the ffmpeg-VNC route works
        without installing extra packages.
      * `virsh screenshot` is known-good and non-intrusive (it does NOT occupy
        the single SPICE client slot the live viewer needs). We therefore poll
        screenshots at `fps` and pipe the PNG stream into ffmpeg's `image2pipe`
        demuxer, which encodes H.264/MP4 on the fly.
      * Audio: a guest using `<audio type='spice'/>` tunnels sound inside SPICE,
        which cannot be tapped on the host. If (and only if) `audio_source` is
        set, ffmpeg reads it (via `audio_format`, e.g. PulseAudio sink monitor or
        an ALSA snd-aloop loopback) and muxes it in. This requires routing guest
        sound to a host-capturable device in the domain XML — see config docs.
        Otherwise recordings are video-only.

    A single instance acts as a singleton recorder: start() refuses to run twice.
    The capture loop runs on a daemon thread so it never blocks the event loop;
    the API calls start()/stop() via run_in_executor.
    """

    def __init__(
        self,
        vm_manager: "VMManager",
        capture_dir: str,
        fps: int = 10,
        max_duration: int = 300,
        audio_source: Optional[str] = None,
        audio_format: str = "pulse",
    ):
        self.vm_manager = vm_manager
        self.capture_dir = Path(capture_dir)
        self.fps = fps
        self.max_duration = max_duration
        self.audio_source = audio_source
        self.audio_format = audio_format

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._active = False
        self._started_at: Optional[float] = None
        self._current_file: Optional[Path] = None
        self._last_error: Optional[str] = None
        self._screen = 0

    # ----- state -----------------------------------------------------------

    def is_recording(self) -> bool:
        with self._lock:
            return self._active

    def get_status(self) -> Dict:
        with self._lock:
            elapsed = (
                (time.time() - self._started_at) if (self._active and self._started_at) else None
            )
            return {
                "recording": self._active,
                "file": self._current_file.name if self._current_file else None,
                "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
                "max_duration": self.max_duration,
                "fps": self.fps,
                "audio": bool(self.audio_source),
                "screen": self._screen,
                "last_error": self._last_error,
            }

    # ----- lifecycle -------------------------------------------------------

    def start(self, screen: int = 0) -> Dict:
        """Start recording a display head. Raises RuntimeError if already recording.

        Args:
            screen: Display head index to record (default 0 = primary).
        """
        with self._lock:
            if self._active:
                raise RuntimeError("A recording is already in progress")

            self._screen = screen
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            suffix = f"-screen{screen}" if screen else ""
            self._current_file = self.capture_dir / f"recording-{ts}{suffix}.mp4"
            self._last_error = None
            self._stop_event.clear()

            cmd = self._build_ffmpeg_cmd(self._current_file)
            audio = "yes" if self.audio_source else "no"
            logger.info("Starting video recording -> %s (audio=%s)", self._current_file, audio)
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._active = True
            self._started_at = time.time()
            self._thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._thread.start()

        return self.get_status()

    def stop(self) -> Dict:
        """Stop recording and finalise the file. Idempotent."""
        with self._lock:
            if not self._active:
                return self.get_status()
            self._stop_event.set()

        thread = self._thread
        if thread:
            thread.join(timeout=15)

        with self._lock:
            result_file = self._current_file.name if self._current_file else None
            self._active = False
            status = self.get_status()
        logger.info(f"Stopped video recording -> {result_file}")
        return status

    def _build_ffmpeg_cmd(self, out_path: Path) -> list:
        """Build the ffmpeg command: PNG frames over stdin (+ optional audio)."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            # Video: piped PNG sequence at the capture fps.
            "-f",
            "image2pipe",
            "-framerate",
            str(self.fps),
            "-i",
            "-",
        ]
        audio_source = self.audio_source
        have_audio = bool(audio_source)
        if audio_source:
            # Audio: a host-capturable source, demuxed by the configured backend
            # (e.g. 'pulse' sink monitor, or 'alsa' snd-aloop loopback device). Bound to a local
            # so the argv cannot carry a None the way `bool(...)`-then-use allowed.
            cmd += ["-f", self.audio_format, "-i", audio_source]

        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            # Drive output frame timing from input fps so 1 PNG == 1 frame.
            "-r",
            str(self.fps),
        ]
        if have_audio:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]

        cmd += [str(out_path)]
        return cmd

    def _capture_loop(self) -> None:
        """Poll screenshots and feed PNG bytes to ffmpeg stdin until stop/cap."""
        interval = 1.0 / self.fps
        deadline = time.time() + self.max_duration
        proc = self._proc
        try:
            while not self._stop_event.is_set():
                if time.time() >= deadline:
                    logger.warning("Recording hit max-duration safety cap; stopping")
                    break
                frame_start = time.time()
                try:
                    png = self.vm_manager.screenshot(self._screen)
                    if proc and proc.stdin:
                        proc.stdin.write(png)
                except (BrokenPipeError, ValueError):
                    break  # ffmpeg died / stdin closed
                except Exception as e:
                    self._last_error = str(e)
                    logger.warning(f"Frame capture error: {e}")
                # Pace to the target fps.
                sleep_for = interval - (time.time() - frame_start)
                if sleep_for > 0:
                    self._stop_event.wait(sleep_for)
        finally:
            self._finalize_proc()

    def _finalize_proc(self) -> None:
        """Signal end-of-stream to ffmpeg and wait for it to flush/mux the MP4.

        communicate() closes/flushes stdin itself, so we must NOT close stdin
        beforehand (doing so triggers a double-flush ValueError).
        """
        proc = self._proc
        if not proc:
            return
        try:
            _, stderr = proc.communicate(timeout=15)
            if proc.returncode not in (0, None) and stderr:
                tail = stderr.decode(errors="replace").strip().splitlines()[-3:]
                if tail:
                    self._last_error = " | ".join(tail)
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        except ValueError:
            # stdin already closed elsewhere; just reap the process.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ----- file listing ----------------------------------------------------

    def list_recordings(self) -> List[Dict]:
        """List finished recordings (newest first)."""
        if not self.capture_dir.exists():
            return []
        files = sorted(
            self.capture_dir.glob("recording-*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        out = []
        current = self._current_file.name if (self._active and self._current_file) else None
        for p in files:
            st = p.stat()
            out.append(
                {
                    "name": p.name,
                    "size_bytes": st.st_size,
                    "modified": st.st_mtime,
                    "in_progress": p.name == current,
                }
            )
        return out

    def recording_path(self, name: str) -> Optional[Path]:
        """Resolve a recording filename to a path, guarding against traversal."""
        if "/" in name or "\\" in name or ".." in name:
            return None
        path = (self.capture_dir / name).resolve()
        try:
            path.relative_to(self.capture_dir.resolve())
        except ValueError:
            return None
        return path if path.exists() else None
