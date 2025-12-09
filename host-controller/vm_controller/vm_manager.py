"""
VM Manager - Controls VM lifecycle, snapshots, and recovery.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module provides the VMManager class for controlling virtual machines via
libvirt, managing snapshots, and implementing automatic recovery mechanisms.
"""

import logging
import subprocess
import time
from typing import List, Optional, Callable

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
        on_reset_callback: Optional[Callable] = None,
    ):
        """
        Initialize VMManager.

        Args:
            vm_name: Name of the VM in libvirt
            snapshot_name: Name of the reference snapshot (default: "ready")
            auto_revert_enabled: Enable automatic revert on failure
            on_reset_callback: Optional callback function to call on VM reset
        """
        self.vm_name = vm_name
        self.snapshot_name = snapshot_name
        self.auto_revert_enabled = auto_revert_enabled
        self.on_reset_callback = on_reset_callback

        logger.info(
            f"Initializing VM Manager for VM '{vm_name}' with snapshot '{snapshot_name}'"
        )

        # Check if snapshot exists
        if not self.snapshot_exists():
            logger.warning(
                f"Snapshot '{snapshot_name}' does not exist. "
                f"VM control will be limited until snapshot is created."
            )

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
        )

        snapshots = [s.strip() for s in result.stdout.split("\n") if s.strip()]
        logger.debug(f"Found {len(snapshots)} snapshots: {snapshots}")
        return snapshots

    def create_snapshot(self, snapshot_name: Optional[str] = None) -> None:
        """
        Create a new snapshot, deleting existing one with same name if present.

        Args:
            snapshot_name: Name for the snapshot (default: use self.snapshot_name)

        Raises:
            subprocess.CalledProcessError: If snapshot creation fails
        """
        name = snapshot_name or self.snapshot_name
        logger.info(f"Creating snapshot '{name}' for VM '{self.vm_name}'")

        # Try to delete existing snapshot (ignore if doesn't exist)
        try:
            subprocess.run(
                ["virsh", "snapshot-delete", self.vm_name, name],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.debug(f"Deleted existing snapshot '{name}'")
        except subprocess.CalledProcessError as e:
            if "No snapshot with name" not in e.stderr:
                logger.warning(f"Could not delete existing snapshot: {e.stderr}")

        # Create new snapshot
        subprocess.run(
            ["virsh", "snapshot-create-as", self.vm_name, name],
            capture_output=True,
            text=True,
            check=True,
        )
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

        subprocess.run(
            ["virsh", "snapshot-delete", self.vm_name, name],
            capture_output=True,
            text=True,
            check=True,
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

        try:
            subprocess.run(
                ["virsh", "destroy", self.vm_name],
                capture_output=True,
                text=True,
                check=True,
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
        logger.info(
            f"Starting VM '{self.vm_name}' by reverting to snapshot '{self.snapshot_name}'"
        )

        # Verify snapshot exists
        if not self.snapshot_exists():
            raise RuntimeError(
                f"Cannot start VM: snapshot '{self.snapshot_name}' does not exist"
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
        )
        logger.info("VM reverted to snapshot and started successfully")

    def check_vm_responsiveness(self, timeout: float = 5.0) -> bool:
        """
        Check if VM is responsive using QEMU guest agent.

        This sends a guest-ping command to the QEMU guest agent running
        inside the VM. Requires qemu-guest-agent to be installed and running
        in the guest.

        Args:
            timeout: Timeout in seconds for the check

        Returns:
            True if VM responds, False otherwise
        """
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
        self, check_interval: float = 10.0, max_attempts: int = 30
    ) -> bool:
        """
        Wait for VM to become responsive after start/revert.

        Polls the VM using QEMU guest agent until it responds or timeout.

        Args:
            check_interval: Seconds between checks
            max_attempts: Maximum number of attempts before giving up

        Returns:
            True if VM became responsive, False if timed out
        """
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
