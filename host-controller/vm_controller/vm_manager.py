"""
VM Manager - Controls VM lifecycle, snapshots, and recovery.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module provides the VMManager class for controlling virtual machines via
libvirt, managing snapshots, and implementing automatic recovery mechanisms.
"""

import ipaddress
import logging
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable, List, Optional

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
        """
        self.vm_name = vm_name
        self.snapshot_name = snapshot_name
        self.auto_revert_enabled = auto_revert_enabled
        self.on_reset_callback = on_reset_callback
        self.startup_wait_interval = startup_wait_interval
        self.startup_max_attempts = startup_max_attempts
        self.qemu_agent_timeout = qemu_agent_timeout

        # Serializes all VM lifecycle mutations (start/stop/restart/snapshot)
        # so an auto-recovery and a manual request can never issue overlapping
        # virsh reverts. Reentrant: restart_vm() calls start_vm() while held.
        self._op_lock = threading.RLock()

        # Discover VM network subnet from libvirt
        self.vm_network = self._discover_vm_network()

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
        """Discover the VM's network subnet from libvirt.
        Returns CIDR notation (e.g. '192.168.122.0/24') or None."""
        try:
            # Get the network name from the VM's interface
            result = subprocess.run(
                ["virsh", "domiflist", self.vm_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None

            network_name = None
            for line in result.stdout.splitlines()[2:]:  # skip header
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "network":
                    network_name = parts[2]
                    break

            if not network_name:
                return None

            # Get the network's IP/netmask
            result = subprocess.run(
                ["virsh", "net-dumpxml", network_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None

            root = ET.fromstring(result.stdout)
            ip_elem = root.find(".//ip")
            if ip_elem is not None:
                addr = ip_elem.get("address")
                netmask = ip_elem.get("netmask")
                if addr and netmask:
                    network = ipaddress.IPv4Network(f"{addr}/{netmask}", strict=False)
                    logger.info(f"Discovered VM network: {network}")
                    return str(network)
        except Exception as e:
            logger.warning(f"Could not discover VM network: {e}")
        return None

    def ensure_vm_network(self) -> Optional[str]:
        """Return the VM subnet, RE-DISCOVERING it if the one-shot probe at init came back empty
        (a transient libvirt hiccup at startup otherwise leaves ``vm_network`` None for the whole
        process — which makes the guest-origin guard fail open forever). Caches on success."""
        if not self.vm_network:
            self.vm_network = self._discover_vm_network()
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
            subprocess.CalledProcessError: If virsh command fails
        """
        logger.debug(f"Listing snapshots for VM '{self.vm_name}'")
        result = subprocess.run(
            ["virsh", "snapshot-list", self.vm_name, "--name"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_VIRSH_QUICK_TIMEOUT_S,
        )

        snapshots = [s.strip() for s in result.stdout.split("\n") if s.strip()]
        logger.debug(f"Found {len(snapshots)} snapshots: {snapshots}")
        return snapshots

    def _delete_snapshot_quiet(self, name: str, children: bool = False) -> None:
        """Best-effort snapshot delete. Swallows a missing-snapshot error, logs anything else.

        Used for cleanup paths where a failure to delete must not itself abort the caller.
        """
        argv = ["virsh", "snapshot-delete", self.vm_name, name]
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
        staging = f"{name}__staging"
        logger.info(f"Creating snapshot '{name}' for VM '{self.vm_name}'")

        with self._op_lock:
            # 1. Clear any staging snapshot left behind by a previously aborted run, then create
            #    the new snapshot under the staging name. The golden '{name}' is still untouched
            #    here, so a failure at this point is fully recoverable.
            self._delete_snapshot_quiet(staging, children=True)
            subprocess.run(
                ["virsh", "snapshot-create-as", self.vm_name, staging],
                capture_output=True,
                text=True,
                check=True,
                timeout=_VIRSH_SLOW_TIMEOUT_S,
            )

            # 2. Verify the staging snapshot is really there before touching the golden one.
            if staging not in self.list_snapshots():
                self._delete_snapshot_quiet(staging, children=True)
                raise RuntimeError(
                    f"Staging snapshot '{staging}' not found after creation - refusing to "
                    f"delete the existing '{name}' snapshot"
                )

            # 3. Retire the old golden and promote the verified state into its place. If the
            #    final create fails, LEAVE the staging snapshot as a recovery point rather than
            #    cleaning it up — better a mis-named snapshot than none at all.
            self._delete_snapshot_quiet(name, children=True)
            try:
                subprocess.run(
                    ["virsh", "snapshot-create-as", self.vm_name, name],
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
            self._delete_snapshot_quiet(staging, children=True)
        logger.info(f"Snapshot '{name}' created successfully")

    def delete_snapshot(self, snapshot_name: Optional[str] = None) -> None:
        """
        Delete a snapshot.

        Args:
            snapshot_name: Name of snapshot to delete (default: use self.snapshot_name)

        Raises:
            subprocess.CalledProcessError: If deletion fails
        """
        name = snapshot_name or self.snapshot_name
        logger.info(f"Deleting snapshot '{name}' for VM '{self.vm_name}'")

        with self._op_lock:
            subprocess.run(
                ["virsh", "snapshot-delete", self.vm_name, name],
                capture_output=True,
                text=True,
                check=True,
                timeout=_VIRSH_OP_TIMEOUT_S,
            )
        logger.info(f"Snapshot '{name}' deleted successfully")

    def stop_vm(self) -> None:
        """
        Stop (destroy) the VM.

        This is a hard stop, equivalent to pulling the power cord.
        The VM will be forcefully terminated.

        Raises:
            subprocess.CalledProcessError: If stop fails (excluding "not running")
        """
        logger.info(f"Stopping VM '{self.vm_name}'")

        with self._op_lock:
            try:
                subprocess.run(
                    ["virsh", "destroy", self.vm_name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=_VIRSH_OP_TIMEOUT_S,
                )
                logger.info("VM stopped successfully")
            except subprocess.CalledProcessError as e:
                if "Domain not running" in e.stderr or "domain is not running" in e.stderr:
                    logger.info("VM was not running")
                else:
                    logger.error(f"Error stopping VM: {e.stderr}")
                    raise

    def start_vm(self) -> None:
        """
        Start the VM by reverting to the ready snapshot.

        This performs a full revert to the configured snapshot, which includes
        starting the VM if it's not running.

        Raises:
            RuntimeError: If snapshot doesn't exist
            subprocess.CalledProcessError: If revert fails
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

                # Revert to snapshot (this also starts the VM)
                subprocess.run(
                    ["virsh", "snapshot-revert", self.vm_name, self.snapshot_name],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=_VIRSH_SLOW_TIMEOUT_S,
                )
                logger.info("VM reverted to snapshot and started successfully")
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

            # Flush conntrack entries for the VM's network to clear stale TCP state.
            # After a snapshot revert, the VM resumes with TCP connections from before
            # the snapshot. The kernel's conntrack table still has the old connection
            # entries, causing packets with mismatched sequence numbers to be dropped.
            self._flush_conntrack()

    def _flush_conntrack(self) -> None:
        """Flush conntrack entries for the VM network to clear stale TCP state.

        After a snapshot revert the VM resumes with TCP connections from before
        the snapshot.  The kernel's conntrack table still has the old entries,
        causing packets with mismatched sequence numbers to be dropped.
        """
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

    def check_vm_responsiveness(self, timeout: Optional[float] = None) -> bool:
        """
        Check if VM is responsive using QEMU guest agent.

        This sends a guest-ping command to the QEMU guest agent running
        inside the VM. Requires qemu-guest-agent to be installed and running
        in the guest.

        Args:
            timeout: Timeout in seconds for the check (default: qemu_agent_timeout)

        Returns:
            True if VM responds, False otherwise
        """
        if timeout is None:
            timeout = self.qemu_agent_timeout
        logger.debug(f"Checking VM '{self.vm_name}' responsiveness via QEMU guest agent")

        try:
            result = subprocess.run(
                [
                    "virsh",
                    "qemu-agent-command",
                    self.vm_name,
                    '{"execute":"guest-ping"}',
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            logger.debug(f"VM is responsive: {result.stdout.strip()}")
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.debug(f"VM is not responsive: {e}")
            return False

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
        Get current VM state from libvirt.

        Returns:
            VM state string (e.g., "running", "shut off", "paused")

        Raises:
            subprocess.CalledProcessError: If state check fails
        """
        result = subprocess.run(
            ["virsh", "domstate", self.vm_name],
            capture_output=True,
            text=True,
            check=True,
            timeout=_VIRSH_QUICK_TIMEOUT_S,
        )

        state = result.stdout.strip()
        logger.debug(f"VM '{self.vm_name}' state: {state}")
        return state

    def is_running(self) -> bool:
        """
        Check if VM is currently running.

        Returns:
            True if VM is running, False otherwise
        """
        try:
            state = self.get_vm_state()
            return state == "running"
        except subprocess.CalledProcessError:
            return False

    def enable_auto_revert(self) -> None:
        """Enable automatic revert on failure."""
        logger.info("Enabling automatic revert")
        self.auto_revert_enabled = True

    def disable_auto_revert(self) -> None:
        """Disable automatic revert (for maintenance)."""
        logger.info("Disabling automatic revert")
        self.auto_revert_enabled = False
