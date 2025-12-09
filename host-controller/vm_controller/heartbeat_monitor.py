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

    Attributes:
        timeout: Seconds without heartbeat before considering VM failed
        check_interval: How often to check for timeout (in seconds)
        enabled: Whether heartbeat monitoring is active
        last_heartbeat: Timestamp of last received heartbeat
    """

    def __init__(
        self,
        timeout: float = 15.0,
        check_interval: float = 0.5,
        on_timeout_callback: Optional[Callable] = None,
    ):
        """
        Initialize HeartbeatMonitor.

        Args:
            timeout: Seconds without heartbeat before failure (default: 15.0)
            check_interval: Seconds between timeout checks (default: 0.5)
            on_timeout_callback: Function to call when timeout occurs
        """
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_timeout_callback = on_timeout_callback

        self.enabled = False
        self.last_heartbeat: Optional[float] = None
        self._check_task: Optional[asyncio.Task] = None

        logger.info(
            f"Initialized HeartbeatMonitor (timeout: {timeout}s, "
            f"check_interval: {check_interval}s)"
        )

    def receive_heartbeat(self) -> None:
        """
        Record a heartbeat signal from the guest.

        Call this method whenever a heartbeat is received from the VM.
        """
        self.last_heartbeat = time.time()
        state = "enabled" if self.enabled else "disabled"
        logger.debug(f"Received heartbeat (monitoring {state})")

    def enable(self) -> None:
        """
        Enable heartbeat monitoring.

        This resets the heartbeat timer and begins checking for timeouts.
        """
        logger.info("Enabling heartbeat monitoring")
        self.enabled = True
        self.last_heartbeat = time.time()

    def disable(self) -> None:
        """
        Disable heartbeat monitoring.

        The monitor will still accept heartbeats but won't trigger timeouts.
        """
        logger.info("Disabling heartbeat monitoring")
        self.enabled = False

    def is_timed_out(self) -> bool:
        """
        Check if heartbeat has timed out.

        Returns:
            True if monitoring is enabled and timeout has elapsed
        """
        if not self.enabled or self.last_heartbeat is None:
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

        return {
            "enabled": self.enabled,
            "timeout": self.timeout,
            "last_heartbeat": self.last_heartbeat,
            "time_since_heartbeat": time_since,
            "is_timed_out": self.is_timed_out(),
            "has_received_heartbeat": self.last_heartbeat is not None,
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
        Internal monitoring loop that checks for timeouts.

        Runs continuously until cancelled, checking for heartbeat timeouts
        at the configured check_interval.
        """
        logger.debug("Heartbeat monitoring loop started")

        try:
            while True:
                if self.is_timed_out():
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

                    # Disable monitoring after timeout to prevent rapid restarts
                    self.disable()

                await asyncio.sleep(self.check_interval)

        except asyncio.CancelledError:
            logger.debug("Heartbeat monitoring loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in heartbeat monitoring loop: {e}", exc_info=True)
            raise

    def reset(self) -> None:
        """
        Reset the heartbeat monitor to initial state.

        This clears the last heartbeat timestamp and disables monitoring.
        Useful when restarting the VM.
        """
        logger.debug("Resetting heartbeat monitor")
        self.last_heartbeat = None
        self.enabled = False
