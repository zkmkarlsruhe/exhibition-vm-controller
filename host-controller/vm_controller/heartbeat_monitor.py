"""
Heartbeat Monitor - Tracks guest heartbeats and triggers recovery.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module implements heartbeat monitoring for virtual machine guests,
detecting timeouts and triggering automatic recovery procedures.
"""

import asyncio
import logging
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """
    Monitors heartbeat signals from VM guest and triggers recovery on failure.

    The monitor expects periodic heartbeat signals from the guest. If no heartbeat
    is received within the timeout period, it can automatically trigger a VM restart.

    Monitoring is active only when auto_revert is enabled on the VMManager.

    Attributes:
        timeout: Seconds without heartbeat before considering VM failed
        check_interval: How often to check for timeout (in seconds)
        last_heartbeat: Timestamp of last received heartbeat
    """

    def __init__(
        self,
        timeout: float = 15.0,
        check_interval: float = 0.5,
        on_timeout_callback: Optional[Callable] = None,
        vm_manager: Optional[object] = None,
    ):
        """
        Initialize HeartbeatMonitor.

        Args:
            timeout: Seconds without heartbeat before failure (default: 15.0)
            check_interval: Seconds between timeout checks (default: 0.5)
            on_timeout_callback: Function to call when timeout occurs
            vm_manager: VMManager instance (required for auto_revert_enabled check)
        """
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_timeout_callback = on_timeout_callback
        self.vm_manager = vm_manager

        self.last_heartbeat: Optional[float] = None
        self._check_task: Optional[asyncio.Task] = None
        self._was_monitoring = False
        self._actual_heartbeat_received = False  # Track if guest actually sent a heartbeat
        self._manual_stop = False  # Track if VM was manually stopped via API

        logger.info(
            f"Initialized HeartbeatMonitor (timeout: {timeout}s, "
            f"check_interval: {check_interval}s, "
            f"vm_state_monitoring: {vm_manager is not None})"
        )

    def receive_heartbeat(self) -> None:
        """
        Record a heartbeat signal from the guest.

        Call this method whenever a heartbeat is received from the VM.
        """
        self.last_heartbeat = time.time()
        self._actual_heartbeat_received = True
        auto_revert = "enabled" if self.vm_manager and self.vm_manager.auto_revert_enabled else "disabled"
        logger.info(f"Received heartbeat from guest (auto-revert {auto_revert})")

    def is_timed_out(self) -> bool:
        """
        Check if heartbeat has timed out.

        Returns:
            True if auto-revert is enabled and timeout has elapsed
        """
        # Only check timeout if auto-revert is enabled
        if not self.vm_manager or not self.vm_manager.auto_revert_enabled:
            return False

        if self.last_heartbeat is None:
            return False

        elapsed = time.time() - self.last_heartbeat
        timed_out = elapsed > self.timeout

        if timed_out:
            logger.warning(
                f"Heartbeat timeout detected: {elapsed:.1f}s since last heartbeat "
                f"(threshold: {self.timeout}s)"
            )

        return timed_out

    def get_time_since_heartbeat(self) -> Optional[float]:
        """
        Get seconds since last heartbeat.

        Returns:
            Seconds since last heartbeat, or None if no heartbeat received yet
        """
        if self.last_heartbeat is None:
            return None

        return time.time() - self.last_heartbeat

    def get_status(self) -> dict:
        """
        Get current heartbeat monitoring status.

        Returns:
            Dictionary with status information
        """
        time_since = self.get_time_since_heartbeat()

        # Monitoring is enabled if auto-revert is enabled
        enabled = self.vm_manager and self.vm_manager.auto_revert_enabled

        return {
            "enabled": enabled,
            "timeout": self.timeout,
            "last_heartbeat": self.last_heartbeat if self._actual_heartbeat_received else None,
            "time_since_heartbeat": time_since,
            "is_timed_out": self.is_timed_out(),
            "has_received_heartbeat": self._actual_heartbeat_received,
        }

    async def start_monitoring(self) -> None:
        """
        Start the async heartbeat monitoring loop.

        This creates a background task that periodically checks for timeouts
        and calls the timeout callback if needed.

        The loop runs until stop_monitoring() is called.
        """
        if self._check_task is not None and not self._check_task.done():
            logger.warning("Heartbeat monitoring already running")
            return

        logger.info("Starting heartbeat monitoring loop")
        self._check_task = asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self) -> None:
        """
        Stop the heartbeat monitoring loop.

        Cancels the background monitoring task.
        """
        logger.info("Stopping heartbeat monitoring loop")

        if self._check_task is not None and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        self._check_task = None

    async def _monitoring_loop(self) -> None:
        """
        Internal monitoring loop that checks for timeouts and VM state.

        Runs continuously until cancelled, checking for:
        - Heartbeat timeouts
        - VM state (if vm_manager is provided)

        Only performs checks when auto-revert is enabled.
        """
        logger.debug("Heartbeat monitoring loop started")

        try:
            while True:
                # Check if auto-revert is enabled
                is_monitoring = self.vm_manager and self.vm_manager.auto_revert_enabled

                if not is_monitoring:
                    # Mark that we're not monitoring
                    self._was_monitoring = False
                    await asyncio.sleep(self.check_interval)
                    continue

                # If we just started monitoring, reset the timer
                if not self._was_monitoring:
                    self.last_heartbeat = time.time()
                    self._was_monitoring = True
                    logger.info("Heartbeat monitoring timer started")

                # Initialize heartbeat timer if not set (shouldn't happen, but just in case)
                if self.last_heartbeat is None:
                    self.last_heartbeat = time.time()

                # Check if VM is running (unless manually stopped)
                if not self._manual_stop:
                    try:
                        is_running = self.vm_manager.is_running()
                        if not is_running:
                            logger.error("VM is not running, triggering recovery")

                            if self.on_timeout_callback:
                                try:
                                    result = self.on_timeout_callback()
                                    if asyncio.iscoroutine(result):
                                        await result
                                except Exception as e:
                                    logger.error(f"Error in VM state recovery callback: {e}", exc_info=True)

                            # Skip heartbeat check this iteration since we already triggered recovery
                            await asyncio.sleep(self.check_interval)
                            continue
                    except Exception as e:
                        logger.debug(f"Error checking VM state: {e}")

                # Check for heartbeat timeout (unless manually stopped)
                if not self._manual_stop and self.is_timed_out():
                    logger.error("Heartbeat timeout detected, triggering recovery")

                    if self.on_timeout_callback:
                        try:
                            # Handle both sync and async callbacks
                            result = self.on_timeout_callback()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"Error in timeout callback: {e}", exc_info=True)
                    else:
                        logger.warning("No timeout callback configured")

                await asyncio.sleep(self.check_interval)

        except asyncio.CancelledError:
            logger.debug("Heartbeat monitoring loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in heartbeat monitoring loop: {e}", exc_info=True)
            raise

    def set_manual_stop(self) -> None:
        """Mark that VM was manually stopped via API."""
        logger.info("VM manually stopped - disabling auto-restart")
        self._manual_stop = True

    def clear_manual_stop(self) -> None:
        """Clear manual stop flag (called when VM is started)."""
        logger.debug("Clearing manual stop flag")
        self._manual_stop = False

    def reset(self) -> None:
        """
        Reset the heartbeat monitor to initial state.

        This clears the last heartbeat timestamp and received flag.
        Useful when restarting the VM.
        """
        logger.debug("Resetting heartbeat monitor")
        self.last_heartbeat = None
        self._actual_heartbeat_received = False
        self._manual_stop = False
