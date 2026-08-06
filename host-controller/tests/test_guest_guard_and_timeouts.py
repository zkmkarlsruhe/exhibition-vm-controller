"""Regression coverage for the 2026-07-25 guest-guard + virsh-timeout fixes.

- ``_deny_from_vm`` used to FAIL OPEN when the VM subnet couldn't be discovered: a single startup
  discovery miss left destructive endpoints (snapshot delete/revert, stop) reachable from the guest
  for the whole process, so stray artwork content could delete the golden snapshot. It now FAILS
  CLOSED (denies) unless ``guest_guard_fail_open`` is set, and re-discovers the subnet on demand.
- Every mutating ``virsh`` call in VMManager runs under ``_op_lock`` and now passes ``timeout=`` so
  a hung libvirt can't wedge the lock forever.
"""

import subprocess
import types

import pytest
from fastapi import HTTPException

import vm_controller.api as api
from vm_controller.vm_manager import VMManager


def _req(ip: str):
    return types.SimpleNamespace(client=types.SimpleNamespace(host=ip))


class _FakeVM:
    def __init__(self, network):
        self._network = network

    def ensure_vm_network(self):
        return self._network

    def is_from_vm(self, ip):
        import ipaddress

        if not self._network:
            return False
        return ipaddress.ip_address(ip) in ipaddress.ip_network(self._network)


def _denied(req) -> int | bool:
    try:
        api._deny_from_vm(req)
        return False
    except HTTPException as e:
        return e.status_code


@pytest.fixture(autouse=True)
def _reset_globals():
    saved = (api.vm_manager, api.config)
    yield
    api.vm_manager, api.config = saved


# --- guest-origin guard ------------------------------------------------------------------------


def test_guard_blocks_requests_from_the_guest_subnet():
    api.vm_manager = _FakeVM("192.168.122.0/24")
    api.config = types.SimpleNamespace(guest_guard_fail_open=False)
    assert _denied(_req("192.168.122.55")) == 403


def test_guard_allows_operator_requests():
    api.vm_manager = _FakeVM("192.168.122.0/24")
    api.config = types.SimpleNamespace(guest_guard_fail_open=False)
    assert _denied(_req("10.0.0.5")) is False


def test_guard_fails_closed_when_subnet_unknown():
    # THE FIX: unknown subnet → deny by default, not allow.
    api.vm_manager = _FakeVM(None)
    api.config = types.SimpleNamespace(guest_guard_fail_open=False)
    assert _denied(_req("10.0.0.5")) == 403


def test_guard_fail_open_opt_in_restores_old_behaviour():
    api.vm_manager = _FakeVM(None)
    api.config = types.SimpleNamespace(guest_guard_fail_open=True)
    assert _denied(_req("10.0.0.5")) is False


# --- virsh timeouts under the op-lock ----------------------------------------------------------


def test_every_virsh_call_passes_a_timeout(monkeypatch):
    calls: list[dict] = []

    def recording_run(cmd, *a, **kw):
        calls.append({"cmd": cmd, "timeout": kw.get("timeout")})
        # snapshot-list must report the 'ready' snapshot so start_vm() takes the revert path
        # (start_vm now fails CLOSED with no ready snapshot instead of a plain `virsh start`).
        out = (
            "ready\n"
            if isinstance(cmd, list) and cmd[:2] == ["virsh", "snapshot-list"]
            else "running\n"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", recording_run)

    vm = VMManager(vm_name="testvm")  # __init__ runs domiflist / net-dumpxml / snapshot-list
    calls.clear()

    vm.get_vm_state()
    vm.list_snapshots()
    vm.stop_vm()
    vm.start_vm()  # reverts to 'ready' (snapshot-revert carries a timeout)

    virsh_calls = [c for c in calls if isinstance(c["cmd"], list) and c["cmd"][:1] == ["virsh"]]
    assert virsh_calls, "expected virsh calls to have been recorded"
    missing = [c["cmd"] for c in virsh_calls if c["timeout"] is None]
    assert not missing, f"virsh calls WITHOUT a timeout (can wedge _op_lock): {missing}"
