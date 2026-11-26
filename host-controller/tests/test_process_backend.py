"""An emulator process under evmc: recovery baseline, host liveness, no guest guard."""

import asyncio
import types

import pytest
from fastapi.testclient import TestClient
from vmctl_core.backends import ProcessBackend, ProcessSpec

import vm_controller.api as api
import vm_controller.heartbeat_monitor as heartbeat_module
from vm_controller.config import Config
from vm_controller.heartbeat_monitor import HeartbeatMonitor
from vm_controller.vm_manager import VMManager


@pytest.fixture
def vm(tmp_path):
    (tmp_path / "disk.img").write_bytes(b"golden")
    backend = ProcessBackend(
        "work",
        instance_dir=tmp_path,
        spec=ProcessSpec(argv=["sleep", "60"], images=["disk.img"]),
    )
    manager = VMManager(vm_name="work", backend=backend)
    yield manager
    backend.stop()


def _req(ip):
    return types.SimpleNamespace(client=types.SimpleNamespace(host=ip), headers={})


def test_no_virsh_is_involved(vm, monkeypatch):
    def forbid(*a, **kw):
        raise AssertionError(f"shell-out in process mode: {a}")

    monkeypatch.setattr("vm_controller.vm_manager.subprocess.run", forbid)
    vm.create_snapshot()
    assert vm.snapshot_exists()
    vm.start_vm()
    assert vm.is_running()
    assert vm.check_vm_responsiveness()
    vm.stop_vm()
    vm.delete_snapshot()
    assert not vm.snapshot_exists()


def test_start_reverts_the_images(vm, tmp_path):
    vm.create_snapshot()
    (tmp_path / "disk.img").write_bytes(b"changed")
    vm.start_vm()
    assert vm.is_running()
    assert (tmp_path / "disk.img").read_bytes() == b"golden"


def test_start_without_a_baseline_stays_closed(vm):
    with pytest.raises(RuntimeError, match="does not exist"):
        vm.start_vm()
    assert not vm.is_running()


def test_operator_is_not_treated_as_a_guest(vm, monkeypatch):
    monkeypatch.setattr(api, "vm_manager", vm)
    api._deny_from_vm(_req("127.0.0.1"))  # no HTTPException


def test_heartbeat_over_http_stays_refused(vm, monkeypatch):
    monkeypatch.setattr(api, "vm_manager", vm)
    with pytest.raises(api.HTTPException):
        api._require_from_vm(_req("127.0.0.1"))


def test_liveness_loop_feeds_the_monitor_only_while_alive(vm, monkeypatch):
    monitor = HeartbeatMonitor(timeout=10, check_interval=1, on_timeout_callback=None)
    beats = []
    monkeypatch.setattr(monitor, "receive_heartbeat", lambda: beats.append(1))
    monkeypatch.setattr(api, "vm_manager", vm)
    monkeypatch.setattr(api, "heartbeat_monitor", monitor)

    async def run():
        task = asyncio.create_task(api._host_liveness_loop())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run())
    assert beats == []
    vm.backend.start()
    asyncio.run(run())
    assert beats == [1]


def test_config_selects_the_backend(monkeypatch):
    monkeypatch.setenv("VMCTL_VM_NAME", "work")
    assert Config().backend == "libvirt"
    monkeypatch.setenv("VMCTL_BACKEND", "process")
    assert Config().backend == "process"


class FakeProbeBackend(ProcessBackend):
    """Boolean ping with the core's separate, tri-state probe detail contract."""

    def __init__(self):
        self.detail = {"alive": None, "status": "ok", "reason": "warming up"}
        self.running = True
        self.reverts = []
        self.pings = 0

    def discover_vm_network(self):
        return None

    def snapshot_list(self):
        return ["ready"]

    def is_running(self):
        return self.running

    def state(self):
        return "running" if self.running else "shut off"

    def guest_ping(self, timeout=5.0):
        self.pings += 1
        return self.detail.get("alive") is True

    def guest_tasks(self):
        return dict(self.detail)

    def snapshot_revert(self, name):
        self.reverts.append(name)
        self.running = False

    def start(self):
        self.running = True


@pytest.mark.parametrize("reason", ["counter stuck", "expected program missing"])
def test_probe_timeout_reverts_ready_but_warmup_does_not(monkeypatch, reason):
    backend = FakeProbeBackend()
    manager = VMManager("work", backend=backend)
    monitor = HeartbeatMonitor(
        timeout=10, vm_manager=manager, on_timeout_callback=api.on_heartbeat_timeout
    )
    manager.on_reset_callback = monitor.reset
    clock = [100.0]
    monkeypatch.setattr(heartbeat_module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(api, "vm_manager", manager)
    monkeypatch.setattr(api, "heartbeat_monitor", monitor)
    monkeypatch.setattr(
        api,
        "config",
        Config(vm_name="work", check_qemu_agent=False, vm_startup_heartbeat_delay=0.01),
    )

    class PollCompleteError(Exception):
        pass

    async def sleep(delay):
        if delay == 1.0:
            raise PollCompleteError
        assert delay == 0.01  # recovery startup delay

    monkeypatch.setattr(api.asyncio, "sleep", sleep)

    async def poll(at):
        clock[0] = at
        with pytest.raises(PollCompleteError):
            await api._host_liveness_loop()
        await monitor._check_once()

    async def run():
        monitor.enable()
        for at in (100, 120, 140):
            await poll(at)
            assert not monitor.is_timed_out()
            assert not monitor._has_received_heartbeat
            assert backend.reverts == []

        backend.detail = {"alive": False, "status": "ok", "reason": reason}
        await poll(141)
        await poll(150)  # exactly the timeout since the last inconclusive sample
        assert backend.reverts == []
        await poll(151)
        assert backend.reverts == ["ready"]
        assert manager.is_running()
        assert monitor.enabled
        assert monitor.last_heartbeat == 151

        backend.detail = {"alive": True, "status": "ok", "reason": "ok"}
        await poll(200)
        assert monitor._has_received_heartbeat
        assert backend.reverts == ["ready"]

        backend.detail = {"alive": False, "status": "ok", "reason": reason}
        await poll(205)  # transient failure must not revert
        backend.detail["alive"] = True
        await poll(210)
        assert backend.reverts == ["ready"]

        monitor.disable()  # neither warm-up nor success may override an operator stop
        backend.detail["alive"] = None
        await poll(300)
        backend.detail["alive"] = True
        await poll(320)
        backend.detail["alive"] = False
        await poll(400)
        assert not monitor.enabled
        assert backend.reverts == ["ready"]

    asyncio.run(run())


@pytest.mark.parametrize("alive", [None, False, True])
def test_status_exposes_cached_probe_without_advancing_it(monkeypatch, alive):
    backend = FakeProbeBackend()
    backend.detail = {
        "alive": alive,
        "status": "ok",
        "kind": "amiga",
        "reason": "warming up" if alive is None else "ok" if alive else "expected program missing",
        "counter": "dispatch",
        "missing": ["artwork"],
        "tasks": [{"name": "Workbench", "command": "SYS:Workbench"}],
    }
    manager = VMManager("work", backend=backend)
    assert manager.check_vm_responsiveness() is alive
    monkeypatch.setattr(api, "vm_manager", manager)
    monkeypatch.setattr(api, "heartbeat_monitor", HeartbeatMonitor())
    client = TestClient(api.app)
    for _ in range(2):
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        assert response.json()["probe"] == backend.detail
    assert backend.pings == 1
