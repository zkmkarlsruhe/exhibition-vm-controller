"""Regression coverage for the fix/bughunt pass.

Each test maps to one confirmed bug in the exhibition VM controller. They are deliberately small
and dependency-free (virsh is faked, no real event loop work beyond asyncio.run) so they can run in
CI without libvirt.
"""

import asyncio
import json
import subprocess
import time
import types

import pytest
from fastapi import HTTPException
from vmctl_core.plugins.runtime import SSE_QUEUE_MAXSIZE

import vm_controller.api as api
from vm_controller.config import Config
from vm_controller.heartbeat_monitor import HeartbeatMonitor
from vm_controller.plugins import PluginRegistry
from vm_controller.vm_manager import VMManager

# --- helpers -----------------------------------------------------------------------------------


def _req(ip: str, headers: dict | None = None):
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=ip),
        headers=headers or {},
    )


def _virsh_parts(cmd):
    """(subcommand, positional args) for a virsh call, whatever shape it arrives in.

    VMManager shells out directly for the staged golden-snapshot refresh, but delegates the
    rest to vmctl_core's LibvirtBackend, which passes a connection flag: `virsh -c <uri> <sub>
    <vm> ...` instead of `virsh <sub> <vm> ...`. Tests assert on the SEQUENCE of operations,
    not on that spelling.
    """
    if not (isinstance(cmd, list) and cmd[:1] == ["virsh"]):
        return None, []
    args = cmd[1:]
    if args[:1] == ["-c"]:
        args = args[2:]
    return (args[0] if args else None), args[1:]


class _FakeVirsh:
    """Stateful fake for subprocess.run driving VMManager's virsh shell-outs."""

    def __init__(self, snaps=None, fail_create=None):
        self.snaps = set(snaps or [])
        self.fail_create = fail_create  # snapshot name whose create-as should fail
        self.calls: list[list[str]] = []

    def run(self, cmd, *a, **kw):
        self.calls.append(cmd)
        if not (isinstance(cmd, list) and cmd[:1] == ["virsh"]):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        sub, rest = _virsh_parts(cmd)
        name = rest[1] if len(rest) > 1 else None  # rest = [<vm>, <snapshot>, *flags]
        if sub == "snapshot-list":
            return subprocess.CompletedProcess(cmd, 0, "\n".join(sorted(self.snaps)) + "\n", "")
        if sub == "snapshot-create-as":
            if name == self.fail_create:
                raise subprocess.CalledProcessError(1, cmd, "", "create failed")
            self.snaps.add(name)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if sub == "snapshot-delete":
            self.snaps.discard(name)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        # domiflist / net-dumpxml / domstate etc.
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture(autouse=True)
def _reset_api_globals():
    saved = (
        api.vm_manager,
        api.config,
        api.heartbeat_monitor,
        api.plugin_registry,
        api._delayed_enable_task,
    )
    yield
    (
        api.vm_manager,
        api.config,
        api.heartbeat_monitor,
        api.plugin_registry,
        api._delayed_enable_task,
    ) = saved


# --- bug 1: snapshot create is atomic (golden never lost) --------------------------------------


def test_create_snapshot_stages_before_touching_golden(monkeypatch):
    fake = _FakeVirsh(snaps={"ready"})
    monkeypatch.setattr(subprocess, "run", fake.run)
    vm = VMManager(vm_name="testvm")
    fake.calls.clear()

    vm.create_snapshot("ready")

    # golden committed, staging cleaned up
    assert fake.snaps == {"ready"}
    creates = [c for c in fake.calls if _virsh_parts(c)[0] == "snapshot-create-as"]
    deletes = [c for c in fake.calls if _virsh_parts(c)[0] == "snapshot-delete"]

    # staging is created BEFORE the old golden is deleted
    def _index_of(sub: str, snapshot: str) -> int:
        for i, c in enumerate(fake.calls):
            s_, rest = _virsh_parts(c)
            if s_ == sub and rest[1:2] == [snapshot]:
                return i
        raise AssertionError(f"no {sub} call for {snapshot!r} in {fake.calls}")

    staging_create = _index_of("snapshot-create-as", "ready__staging")
    golden_delete = _index_of("snapshot-delete", "ready")
    assert staging_create < golden_delete
    assert creates and deletes


def test_create_snapshot_keeps_recovery_point_when_final_create_fails(monkeypatch):
    # THE FIX: if the final create fails, the verified staging snapshot is LEFT as a recovery
    # point — there is never a moment with zero ready-state snapshots.
    fake = _FakeVirsh(snaps={"ready"}, fail_create="ready")
    monkeypatch.setattr(subprocess, "run", fake.run)
    vm = VMManager(vm_name="testvm")

    with pytest.raises(subprocess.CalledProcessError):
        vm.create_snapshot("ready")

    assert "ready__staging" in fake.snaps  # recovery point survived
    assert fake.snaps, "must never end with zero snapshots"


# --- bug 2: generation token stops a stale re-arm overriding an operator stop -------------------


def test_enable_if_current_ignores_stale_rearm():
    hb = HeartbeatMonitor()
    hb.disable()
    stale_gen = hb.generation
    hb.disable()  # operator /vm/stop bumps the token again
    assert hb.enable_if_current(stale_gen) is False
    assert hb.enabled is False


def test_enable_if_current_arms_when_token_matches():
    hb = HeartbeatMonitor()
    hb.disable()
    assert hb.enable_if_current(hb.generation) is True
    assert hb.enabled is True


# --- bug 4: proxy-aware client IP resolution ----------------------------------------------------


def test_client_ip_uses_x_real_ip_behind_trusted_proxy():
    api.config = types.SimpleNamespace(trusted_proxies=["127.0.0.1"])
    req = _req("127.0.0.1", {"x-real-ip": "192.168.122.55"})
    assert api._client_ip(req) == "192.168.122.55"


def test_client_ip_ignores_headers_from_untrusted_peer():
    api.config = types.SimpleNamespace(trusted_proxies=["127.0.0.1"])
    req = _req("192.168.122.9", {"x-real-ip": "10.0.0.1"})  # guest trying to spoof
    assert api._client_ip(req) == "192.168.122.9"


def test_client_ip_ignores_x_real_ip_from_docker_bridge_peer():
    # audit 2026-10-30 #2: a containerised nginx reaches the controller from the Docker bridge
    # gateway (e.g. 172.17.0.1), which is NOT in the default trusted_proxies (loopback only). The
    # forwarded guest address is then ignored and every request looks like the bridge gateway:
    # heartbeat 403, guest guard bypassed. See conservation/README.md ("Front door in Docker").
    default_trusted = Config.model_fields["trusted_proxies"].default
    api.config = types.SimpleNamespace(trusted_proxies=default_trusted)
    req = _req("172.17.0.1", {"x-real-ip": "192.168.122.55"})
    assert api._client_ip(req) == "172.17.0.1"


def test_guard_blocks_guest_behind_nginx():
    # Without the fix the guard sees 127.0.0.1 (the proxy) and lets guest content through.
    api.vm_manager = _FakeVMManager(network="192.168.122.0/24")
    api.config = types.SimpleNamespace(guest_guard_fail_open=False, trusted_proxies=["127.0.0.1"])
    req = _req("127.0.0.1", {"x-real-ip": "192.168.122.55"})
    with pytest.raises(HTTPException) as exc:
        api._deny_from_vm(req)
    assert exc.value.status_code == 403


# --- bug 8: recovery backoff (edge-triggering) --------------------------------------------------


def test_recovery_backoff_throttles_repeated_triggers():
    triggers = {"n": 0}

    async def cb():
        triggers["n"] += 1

    class _DownVM:
        def is_running(self):
            return False

    async def drive():
        hb = HeartbeatMonitor(
            check_interval=0.01, recovery_backoff=0.2, on_timeout_callback=cb, vm_manager=_DownVM()
        )
        hb.enable()
        for _ in range(6):
            await hb._check_once()
        first = triggers["n"]
        await asyncio.sleep(0.25)
        await hb._check_once()
        return first, triggers["n"]

    first, second = asyncio.run(drive())
    assert first == 1  # six rapid checks -> a single recovery attempt
    assert second == 2  # after the backoff window elapses, one more is allowed


# --- bug 11: monitoring loop survives an unexpected exception -----------------------------------


def test_monitoring_loop_survives_exception_and_keeps_running():
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient glitch")

    async def drive():
        hb = HeartbeatMonitor(check_interval=0.01)
        hb._check_once = boom  # first iteration raises
        await hb.start_monitoring()
        await asyncio.sleep(0.1)
        alive = not hb._check_task.done()
        await hb.stop_monitoring()
        return alive, calls["n"]

    alive, n = asyncio.run(drive())
    assert alive  # the sole recovery loop was NOT killed by the exception
    assert n > 1  # it kept calling after the first raise


# --- bug 10: from_yaml rejects a non-mapping document -------------------------------------------


def test_from_yaml_rejects_top_level_list(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="top-level mapping"):
        Config.from_yaml(cfg)


# --- bug 7: SSE queue is bounded and drops the oldest event -------------------------------------
# The cap and the drop-oldest step now live in the shared registry (vmctl_core.plugins), which
# this one subclasses; the test stays here because the guarantee is an exhibition requirement —
# a kiosk tab that stops reading must not grow a queue over a run of weeks.


def test_push_event_bounds_queue_by_dropping_oldest():
    async def drive():
        reg = PluginRegistry()
        q = reg.sse_connect()
        for i in range(SSE_QUEUE_MAXSIZE):
            q.put_nowait(("e", str(i)))
        reg.push_event("e", "newest")  # queue is full -> drop oldest, keep newest
        return q

    q = asyncio.run(drive())
    assert q.qsize() == SSE_QUEUE_MAXSIZE  # stayed bounded
    assert q.get_nowait() == ("e", "1")  # ("e", "0") was dropped


# --- api handler behaviour (bugs 3, 5, 6) ------------------------------------------------------


class _FakeVMManager:
    """Minimal VMManager stand-in for exercising the API handlers."""

    host_liveness = False

    def __init__(self, network="192.168.122.0/24", restart_result=True, start_hook=None):
        self.vm_name = "testvm"
        self._network = network
        self._restart_result = restart_result
        self._start_hook = start_hook
        self.stop_should_fail = False

    def ensure_vm_network(self):
        return self._network

    def is_from_vm(self, ip):
        import ipaddress

        if not self._network:
            return False
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(self._network)
        except ValueError:
            return False

    def restart_vm(self, wait_for_ready=True):
        return self._restart_result

    def start_vm(self):
        if self._start_hook:
            self._start_hook()

    def stop_vm(self):
        if self.stop_should_fail:
            raise RuntimeError("destroy failed")


def _base_config():
    return types.SimpleNamespace(
        check_qemu_agent=True,
        vm_startup_heartbeat_delay=0.01,
        guest_guard_fail_open=False,
        trusted_proxies=[],
    )


def test_restart_false_surfaces_error_and_restores_monitoring():
    # bug 6 + bug 3: a restart that never reaches ready must 500, skip re-arm, and NOT leave the
    # monitor disabled (blind).
    api.vm_manager = _FakeVMManager(restart_result=False)
    api.config = _base_config()
    api.plugin_registry = None
    api.heartbeat_monitor = HeartbeatMonitor()
    api.heartbeat_monitor.enable()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.restart_vm(_req("10.0.0.5")))
    assert exc.value.status_code == 500
    assert api.heartbeat_monitor.enabled is True  # restored, not blind


def test_failed_auto_recovery_rearms_the_timeout_clock():
    # audit 2026-10-30 #1: on_heartbeat_timeout used to `return` early when restart_vm() came
    # back False (guest never QEMU-agent ready). start_vm() had already reset() the monitor
    # (last_heartbeat=None), and is_timed_out() is False while None — so the hung exhibit was
    # never retried. The callback must fall through to reset/enable so the timeout can fire again.
    class _HungVM(_FakeVMManager):
        auto_revert_enabled = True

        def restart_vm(self, wait_for_ready=True):
            api.heartbeat_monitor.reset()  # what the real start_vm() does via on_reset_callback
            return False

    api.vm_manager = _HungVM()
    api.config = _base_config()
    api.heartbeat_monitor = HeartbeatMonitor(timeout=0.05)
    api.heartbeat_monitor.enable()

    asyncio.run(api.on_heartbeat_timeout())
    assert api.heartbeat_monitor.enabled is True
    assert api.heartbeat_monitor.last_heartbeat is not None  # clock re-armed, not left at None
    time.sleep(0.1)
    assert api.heartbeat_monitor.is_timed_out() is True  # a still-hung guest is caught again


def test_refused_guest_restart_does_not_burn_the_rate_limit_window(monkeypatch):
    # audit 2026-10-30 #6: the rate-limit stamp was taken BEFORE the plugin gate, so a restart the
    # plugin refused (403) still started the 60 s window and the next, legitimate guest request
    # got a 429. The stamp must land only after the plugin check and a successful revert.
    class _Plugins:
        def __init__(self):
            self.reason = "phase bigbang"

        def check_pre_restart(self):
            return self.reason

        def run_post_restart(self):
            pass

    monkeypatch.setattr(api, "_last_guest_restart", 0.0)
    api.vm_manager = _FakeVMManager()
    api.config = _base_config()
    api.plugin_registry = plugins = _Plugins()
    api.heartbeat_monitor = HeartbeatMonitor()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.restart_vm(_req("192.168.122.10")))
    assert exc.value.status_code == 403
    assert api._last_guest_restart == 0.0  # refused -> window untouched

    plugins.reason = None
    asyncio.run(api.restart_vm(_req("192.168.122.10")))  # must not be 429
    assert api._last_guest_restart > 0.0  # stamped after the successful revert

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.restart_vm(_req("192.168.122.10")))
    assert exc.value.status_code == 429


def test_unknown_subnet_treats_restart_caller_as_guest(monkeypatch):
    # audit 2026-10-30 #6: with the subnet unknown is_from_vm() is False, so a guest used to get
    # the operator path (no rate limit, no plugin gate) while every other endpoint failed closed.
    monkeypatch.setattr(api, "_last_guest_restart", 0.0)
    api.vm_manager = _FakeVMManager(network=None)
    api.config = _base_config()
    api.plugin_registry = None
    api.heartbeat_monitor = HeartbeatMonitor()

    asyncio.run(api.restart_vm(_req("10.0.0.5")))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.restart_vm(_req("10.0.0.5")))
    assert exc.value.status_code == 429


def test_shell_hook_signal_event_is_valid_json(tmp_path, monkeypatch):
    # audit 2026-10-30 #13: the SSE payload for a shell-hook signal was hand-built with %s, so a
    # value containing a quote produced invalid JSON and the browser's JSON.parse threw.
    hook = tmp_path / "signals" / "ui-state.sh"
    hook.parent.mkdir()
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    reg = PluginRegistry(plugins_dir=tmp_path / "plugins", hooks_dir=tmp_path)
    reg._sse_clients = [object()]  # non-empty: push_event is only called with clients attached
    pushed = []
    monkeypatch.setattr(reg, "push_event", lambda event, data: pushed.append((event, data)))

    assert reg.handle_signal("ui-state", 'say "hi"') is True
    assert json.loads(pushed[0][1]) == {"event": "ui-state", "value": 'say "hi"'}


def test_start_keeps_monitor_disabled_during_revert():
    # bug 5: monitoring must stay disabled while start_vm() reverts, then re-arm afterwards.
    seen = {}

    def hook():
        seen["enabled_during_revert"] = api.heartbeat_monitor.enabled

    api.vm_manager = _FakeVMManager(start_hook=hook)
    api.config = _base_config()
    api.plugin_registry = None
    api.heartbeat_monitor = HeartbeatMonitor()
    api.heartbeat_monitor.enable()

    asyncio.run(api.start_vm(_req("10.0.0.5")))
    assert seen["enabled_during_revert"] is False  # no concurrent recovery mid-revert
    assert api.heartbeat_monitor.enabled is True  # re-armed after boot delay


def test_stop_restores_monitoring_on_failure():
    # bug 3: a failed destroy must not leave the controller permanently blind.
    vm = _FakeVMManager()
    vm.stop_should_fail = True
    api.vm_manager = vm
    api.config = _base_config()
    api.heartbeat_monitor = HeartbeatMonitor()
    api.heartbeat_monitor.enable()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.stop_vm(_req("10.0.0.5")))
    assert exc.value.status_code == 500
    assert api.heartbeat_monitor.enabled is True  # restored


def test_stop_success_disables_monitoring():
    api.vm_manager = _FakeVMManager()
    api.config = _base_config()
    api.heartbeat_monitor = HeartbeatMonitor()
    api.heartbeat_monitor.enable()

    asyncio.run(api.stop_vm(_req("10.0.0.5")))
    assert api.heartbeat_monitor.enabled is False  # intentional stop stays stopped


# --- flow-bugs pass ----------------------------------------------------------------------------


def test_slow_recovery_does_not_immediately_requeue_second_revert():
    # HIGH bug 2: _last_recovery_attempt was stamped at recovery START, so a recovery that
    # outlasts recovery_backoff left the cooldown already expired — the very next check queued a
    # SECOND revert on top of the still-booting guest. Restamping AFTER recovery keeps the guest
    # in cooldown for the whole restart.
    triggers = {"n": 0}

    async def slow_cb():
        triggers["n"] += 1
        await asyncio.sleep(0.15)  # recovery outlasts the 0.05s backoff below

    class _DownVM:
        def is_running(self):
            return False

    async def drive():
        hb = HeartbeatMonitor(
            check_interval=0.01,
            recovery_backoff=0.05,
            on_timeout_callback=slow_cb,
            vm_manager=_DownVM(),
        )
        hb.enable()
        await hb._check_once()  # triggers recovery (0.15s); restamp lands after it returns
        first = triggers["n"]
        await hb._check_once()  # immediately after — must still be inside the cooldown window
        return first, triggers["n"]

    first, second = asyncio.run(drive())
    assert first == 1
    assert second == 1  # no double-revert despite recovery outlasting the backoff


def test_start_vm_fails_closed_without_ready_snapshot(monkeypatch):
    # HIGH bug 3: with no 'ready' snapshot start_vm() used to fall back to a plain `virsh start`,
    # booting mutable/tampered disk state. It must now refuse (fail closed).
    fake = _FakeVirsh(snaps=set())  # no 'ready'
    monkeypatch.setattr(subprocess, "run", fake.run)
    vm = VMManager(vm_name="art", snapshot_name="ready")
    with pytest.raises(RuntimeError, match="refusing to boot"):
        vm.start_vm()
    assert not any(
        isinstance(c, list) and c[:2] == ["virsh", "start"] for c in fake.calls
    )  # never booted dirty state


def test_heartbeat_rejected_from_non_guest_source():
    # HIGH bug 9: an external caller must NOT be able to hold the watchdog open over a dead
    # exhibit — heartbeats are bound to the guest network.
    api.vm_manager = _FakeVMManager(network="192.168.122.0/24")
    api.config = _base_config()
    api.plugin_registry = None
    api.heartbeat_monitor = HeartbeatMonitor()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.receive_heartbeat(_req("10.0.0.5")))
    assert exc.value.status_code == 403
    assert api.heartbeat_monitor._has_received_heartbeat is False  # not counted


def test_heartbeat_accepted_from_guest_network():
    api.vm_manager = _FakeVMManager(network="192.168.122.0/24")
    api.config = _base_config()
    api.plugin_registry = None
    api.heartbeat_monitor = HeartbeatMonitor()

    asyncio.run(api.receive_heartbeat(_req("192.168.122.50")))
    assert api.heartbeat_monitor._has_received_heartbeat is True


def test_heartbeat_fails_closed_when_subnet_unknown():
    # Unknown subnet → reject (watchdog free to revert), unless guest_guard_fail_open.
    api.vm_manager = _FakeVMManager(network=None)
    api.config = _base_config()
    api.plugin_registry = None
    api.heartbeat_monitor = HeartbeatMonitor()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.receive_heartbeat(_req("192.168.122.50")))
    assert exc.value.status_code == 403


def test_log_level_accepts_lowercase_from_older_configs():
    assert Config(vm_name="x", log_level="info").log_level == "INFO"
    with pytest.raises(ValueError):
        Config(vm_name="x", log_level="INFOO")
