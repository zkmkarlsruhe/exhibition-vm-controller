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
start semantics, auto-revert/heartbeat readiness, and the operator network guard.
Dependency arrow: exhibition -> vmctl-core only.
"""

import ipaddress
import logging
import subprocess
import threading
import time
from collections.abc import Callable

from vmctl_core.backends import LibvirtBackend, ProcessBackend

logger = logging.getLogger(__name__)


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
        on_reset_callback: Callable | None = None,
        startup_wait_interval: float = 10.0,
        startup_max_attempts: int = 30,
        qemu_agent_timeout: float = 5.0,
        uri: str = "qemu:///system",
        backend: ProcessBackend | None = None,
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
        self.probe_detail: dict | None = None

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

    def _discover_vm_network(self) -> str | None:
        """Discover the VM's network subnet from libvirt (via the shared backend).
        Returns CIDR notation (e.g. '192.168.122.0/24') or None."""
        try:
            return self.backend.discover_vm_network()
        except Exception as e:
            logger.warning(f"Could not discover VM network: {e}")
            return None

    def ensure_vm_network(self) -> str | None:
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

    def list_snapshots(self) -> list[str]:
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

    def create_snapshot(self, snapshot_name: str | None = None) -> None:
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
            BackendError: If snapshot creation or staging verification fails
        """
        name = snapshot_name or self.snapshot_name
        with self._op_lock:
            if isinstance(self.backend, LibvirtBackend):
                self.backend.refresh_snapshot(name, timeout=120)
            else:
                self.backend.snapshot_create(name)
        logger.info(f"Snapshot '{name}' created successfully")

    def delete_snapshot(self, snapshot_name: str | None = None) -> None:
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

    def check_vm_responsiveness(self, timeout: float | None = None) -> bool | None:
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
        self, check_interval: float | None = None, max_attempts: int | None = None
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
            BackendError: If restart fails
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
