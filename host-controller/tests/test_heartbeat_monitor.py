"""Regression coverage for the 2026-07-25 heartbeat fixes.

- Auto-recovery used to call ``reset()`` (last_heartbeat=None → is_timed_out() forever False), so a
  guest that booted but never sent another heartbeat was never re-caught. The recovery path now
  ``enable()``s after ``reset()`` to re-ARM the clock.
- The monitoring loop called the BLOCKING ``vm_manager.is_running()`` (virsh domstate) directly on
  the event loop; it now runs it in an executor so a slow libvirt probe can't freeze heartbeat
  intake / the whole API.
"""

import asyncio
import threading
import time

from vm_controller.heartbeat_monitor import HeartbeatMonitor


def test_reset_alone_does_not_arm_the_clock():
    hb = HeartbeatMonitor(timeout=0.1, check_interval=0.05)
    hb.enable()
    hb.reset()  # the pre-fix recovery state
    assert hb.last_heartbeat is None
    time.sleep(0.15)
    assert hb.is_timed_out() is False  # documents WHY reset-only silently disabled detection


def test_reset_then_enable_rearms_a_silent_guest():
    hb = HeartbeatMonitor(timeout=0.15, check_interval=0.05)
    hb.reset()
    hb.enable()  # the FIX
    assert hb._enabled and hb.last_heartbeat is not None
    time.sleep(0.2)
    assert hb.is_timed_out() is True  # a booted-but-silent guest is caught again


def test_monitoring_loop_runs_is_running_off_the_event_loop():
    main_tid = threading.get_ident()
    seen: dict[str, int] = {}

    class SlowVM:
        def is_running(self):
            seen["tid"] = threading.get_ident()
            time.sleep(0.15)  # simulate a slow virsh domstate
            return True

    async def drive():
        hb = HeartbeatMonitor(timeout=5, check_interval=0.05, vm_manager=SlowVM())
        hb.enable()
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0.02)
                ticks += 1

        await hb.start_monitoring()
        await ticker()
        await hb.stop_monitoring()
        return ticks

    ticks = asyncio.run(drive())
    # the blocking probe ran in a WORKER thread, and the loop kept ticking meanwhile
    assert seen.get("tid") is not None and seen["tid"] != main_tid
    assert ticks >= 15, f"event loop stalled during the blocking probe (ticks={ticks})"
