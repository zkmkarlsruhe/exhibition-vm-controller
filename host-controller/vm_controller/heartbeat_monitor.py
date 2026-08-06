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
from typing import Callable, Optional

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
        recovery_backoff: float = 30.0,
    ):
        self.timeout = timeout
        self.check_interval = check_interval
        self.on_timeout_callback = on_timeout_callback
        self.vm_manager = vm_manager
        self.recovery_backoff = recovery_backoff

        self.last_heartbeat: Optional[float] = None
        self._enabled = False
        self._has_received_heartbeat = False
        self._check_task: Optional[asyncio.Task] = None

        # Monotonically increasing token bumped on every disable(). A deferred re-arm
        # (post-boot delay in the API, or the post-recovery re-arm) captures the token
        # BEFORE it sleeps and only re-enables if it still matches — so an operator
        # /vm/stop issued during that sleep can't be silently overridden.
        self._generation = 0

        # Edge-trigger guard for recovery. Without it the loop re-fires recovery every
        # check_interval (0.5s) while the VM stays down / auto_revert is disabled, flooding
        # the journal and hammering virsh. We only re-attempt after recovery_backoff seconds.
        self._last_recovery_attempt = 0.0

        logger.info(
            f"Initialized HeartbeatMonitor (timeout: {timeout}s, "
            f"check_interval: {check_interval}s, "
            f"recovery_backoff: {recovery_backoff}s, "
            f"vm_state_monitoring: {vm_manager is not None})"
        )

    @property
    def enabled(self) -> bool:
        """Whether the monitor is currently armed."""
        return self._enabled

    def enable(self) -> None:
        """Enable heartbeat monitoring. Resets the timer."""
        self._enabled = True
        self.last_heartbeat = time.time()
        logger.info("Heartbeat monitoring enabled")

    def disable(self) -> None:
        """Disable heartbeat monitoring.

        Bumps the generation token so any deferred re-arm scheduled before this call
        becomes stale and is ignored (see ``enable_if_current``).
        """
        self._enabled = False
        self._generation += 1
        logger.info("Heartbeat monitoring disabled")

    @property
    def generation(self) -> int:
        """Current desired-state token. Capture before a deferred re-arm."""
        return self._generation

    def enable_if_current(self, generation: int) -> bool:
        """Re-enable only if ``generation`` still matches the current token.

        Returns True if the monitor was (re-)enabled, False if the request was stale
        (an operator disabled monitoring in the meantime — e.g. a /vm/stop during the
        post-boot delay) and must not override that intent.
        """
        if generation != self._generation:
            logger.info(
                "Stale heartbeat re-arm ignored (desired state changed since it was scheduled)"
            )
            return False
        self.enable()
        return True

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

    def _in_recovery_cooldown(self) -> bool:
        """True while we are still inside the backoff window after a recovery attempt."""
        return (time.time() - self._last_recovery_attempt) < self.recovery_backoff

    async def _check_once(self) -> None:
        """Run a single monitoring check. Raises nothing that should kill the loop."""
        if not self._enabled:
            return

        # Check VM state. is_running() shells out to `virsh domstate` (blocking) — run it in a
        # thread so a slow/hung libvirt probe can't freeze THIS event loop, which also serves
        # heartbeat intake and SSE. Running it inline would stall the whole API while virsh hangs.
        if self.vm_manager:
            try:
                loop = asyncio.get_running_loop()
                running = await loop.run_in_executor(None, self.vm_manager.is_running)
            except Exception as e:
                logger.debug(f"Error checking VM state: {e}")
                running = True  # a probe error is not proof the VM is down; fall through
            if not running:
                if not self._in_recovery_cooldown():
                    logger.error("VM is not running, triggering recovery")
                    self._last_recovery_attempt = time.time()
                    await self._trigger_recovery()
                    # Recovery (revert + boot + ready-wait) can outlast recovery_backoff. Restamp
                    # AFTER it returns so the cooldown covers the whole restart — otherwise the
                    # window has already elapsed by the time the op lock frees and the very next
                    # tick queues a SECOND revert on top of a still-booting guest (double-revert).
                    self._last_recovery_attempt = time.time()
                return

        # Check heartbeat timeout
        if self.is_timed_out():
            if not self._in_recovery_cooldown():
                logger.error(
                    f"Heartbeat timeout: {time.time() - self.last_heartbeat:.1f}s "
                    f"(threshold: {self.timeout}s)"
                )
                self._last_recovery_attempt = time.time()
                await self._trigger_recovery()
                # Restamp AFTER recovery so the backoff window covers a slow restart+ready wait
                # (see the VM-state branch above) — no immediate second revert on a slow boot.
                self._last_recovery_attempt = time.time()

    async def _monitoring_loop(self) -> None:
        # This is the SOLE recovery loop for the whole show. A single unexpected exception must
        # not kill it — catch-and-continue per iteration so one bad tick (e.g. a transient virsh
        # glitch) can't leave the exhibit un-monitored for the rest of the run.
        while True:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in heartbeat monitoring loop: {e}", exc_info=True)
            await asyncio.sleep(self.check_interval)

    async def _trigger_recovery(self) -> None:
        if self.on_timeout_callback:
            try:
                result = self.on_timeout_callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Error in recovery callback: {e}", exc_info=True)
