"""
Heartbeat Monitor - Tracks guest heartbeats and triggers recovery.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT
"""

import asyncio
import logging
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """Monitors heartbeat signals from VM guest and triggers recovery on timeout.

    Simple enable/disable control:
    - disabled: monitoring loop runs but doesn't check timeouts or VM state
    - enabled: checks for heartbeat timeout and VM state, triggers recovery
    """

    def __init__(
        self,
        timeout: float = 15.0,
        check_interval: float = 0.5,
        on_timeout_callback: Optional[Callable] = None,
        vm_manager: Optional[object] = None,
    ):
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_timeout_callback = on_timeout_callback
        self.vm_manager = vm_manager

        self.last_heartbeat: Optional[float] = None
        self._enabled = False
        self._has_received_heartbeat = False
        self._check_task: Optional[asyncio.Task] = None

        logger.info(
            f"Initialized HeartbeatMonitor (timeout: {timeout}s, "
            f"check_interval: {check_interval}s, "
            f"vm_state_monitoring: {vm_manager is not None})"
        )

    def enable(self) -> None:
        """Enable heartbeat monitoring. Resets the timer."""
        self._enabled = True
        self.last_heartbeat = time.time()
        logger.info("Heartbeat monitoring enabled")

    def disable(self) -> None:
        """Disable heartbeat monitoring."""
        self._enabled = False
        logger.info("Heartbeat monitoring disabled")

    def reset(self) -> None:
        """Reset state (after VM revert). Keeps enabled/disabled as-is."""
        self.last_heartbeat = None
        self._has_received_heartbeat = False
        logger.debug("Heartbeat monitor reset")

    def receive_heartbeat(self) -> None:
        """Record a heartbeat from the guest."""
        self.last_heartbeat = time.time()
        self._has_received_heartbeat = True

    def is_timed_out(self) -> bool:
        if not self._enabled or self.last_heartbeat is None:
            return False
        return (time.time() - self.last_heartbeat) > self.timeout

    def get_status(self) -> dict:
        time_since = (time.time() - self.last_heartbeat) if self.last_heartbeat else None
        return {
            "enabled": self._enabled,
            "timeout": self.timeout,
            "last_heartbeat": self.last_heartbeat if self._has_received_heartbeat else None,
            "time_since_heartbeat": time_since,
            "is_timed_out": self.is_timed_out(),
            "has_received_heartbeat": self._has_received_heartbeat,
        }

    async def start_monitoring(self) -> None:
        if self._check_task is not None and not self._check_task.done():
            return
        logger.info("Starting heartbeat monitoring loop")
        self._check_task = asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self) -> None:
        logger.info("Stopping heartbeat monitoring loop")
        if self._check_task is not None and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        self._check_task = None

    async def _monitoring_loop(self) -> None:
        try:
            while True:
                if self._enabled:
                    # Check VM state
                    if self.vm_manager:
                        try:
                            if not self.vm_manager.is_running():
                                logger.error("VM is not running, triggering recovery")
                                await self._trigger_recovery()
                                await asyncio.sleep(self.check_interval)
                                continue
                        except Exception as e:
                            logger.debug(f"Error checking VM state: {e}")

                    # Check heartbeat timeout
                    if self.is_timed_out():
                        logger.error(
                            f"Heartbeat timeout: {time.time() - self.last_heartbeat:.1f}s "
                            f"(threshold: {self.timeout}s)"
                        )
                        await self._trigger_recovery()

                await asyncio.sleep(self.check_interval)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in heartbeat monitoring loop: {e}", exc_info=True)
            raise

    async def _trigger_recovery(self) -> None:
        if self.on_timeout_callback:
            try:
                result = self.on_timeout_callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Error in recovery callback: {e}", exc_info=True)
