"""Regression coverage for the flow-bugs pass on the conservation layer + guest file helpers.

These modules are flat scripts (no package __init__), so they are loaded by file path. serve_archive
(bug 11) is not unit-tested here because importing it needs mitmproxy; its fix (don't record >=400
responses) is a one-line status gate.
"""

import importlib.util
import subprocess
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


archive_store = _load("conservation/proxy/archive_store.py", "cons_archive_store")
hosts = _load("conservation/kiosk/hosts.py", "cons_hosts")
broker = _load("conservation/host/virsh-broker.py", "cons_virsh_broker")
legacy_agent = _load("conservation/guest_agent/legacy_agent.py", "cons_legacy_agent")


# --- bug 10: legacy agent exposes the tools the kiosk/proxy callers use -------------------------


def test_legacy_agent_registers_conservation_tools():
    for name in (
        "env",
        "find_files",
        "reg_get",
        "reg_set",
        "reg_delete",
        "show_window",
        "install_cert",
    ):
        assert name in legacy_agent.TOOLS  # no longer fails with 'unknown tool' on XP guests


def test_legacy_agent_env_and_find_files(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("y")
    assert isinstance(legacy_agent.tool_env({}), dict)
    res = legacy_agent.tool_find_files({"dir": str(tmp_path), "pattern": "*.txt"})
    assert res["count"] == 2 and all(f.endswith(".txt") for f in res["files"])


def test_legacy_agent_windows_only_tools_give_clear_error_off_windows():
    # off-Windows these must return an actionable __error__ via _call_tool, NOT a silent
    # 'unknown tool' (the pre-fix failure mode).
    out = legacy_agent._call_tool("reg_get", {"hive": "HKCU", "key": "X"})
    assert out["isError"] is True and "unknown tool" not in out["content"][0]["text"]


# --- bug 5: archive path traversal -------------------------------------------------------------


def test_archive_paths_neutralise_dotdot_in_path(tmp_path):
    body, meta = archive_store.paths(str(tmp_path), "evil.example", "/../../etc/passwd")
    root = tmp_path.resolve()
    assert ".." not in Path(body).parts  # traversal token neutralised, not carried through
    assert str(root) in str(Path(body).resolve())  # stays confined under the archive root


def test_archive_paths_neutralise_dotdot_host(tmp_path):
    body, _ = archive_store.paths(str(tmp_path), "..", "/logo.png")
    assert ".." not in Path(body).parts
    assert str(tmp_path.resolve()) in str(Path(body).resolve())


# --- bug 6: hosts-file read error must abort the write -----------------------------------------


class _AgReadFails:
    def __init__(self):
        self.writes = []

    def call(self, name, args):
        if name == "read_file":
            return {}, "access denied"  # guest read failed
        if name == "write_file":
            self.writes.append(args)
            return {}, None
        return {}, None


def test_hosts_add_aborts_on_read_error():
    ag = _AgReadFails()
    ok, _msg = hosts.add(ag, "artwork.local", "192.168.122.1")
    assert ok is False
    assert ag.writes == []  # NEVER clobber the hosts file from a failed/partial read


def test_hosts_remove_aborts_on_read_error():
    ag = _AgReadFails()
    ok, _msg = hosts.remove(ag, "artwork.local")
    assert ok is False
    assert ag.writes == []


class _AgOK:
    def __init__(self, text):
        self.text = text
        self.written = None

    def call(self, name, args):
        if name == "read_file":
            return {"text": self.text}, None
        if name == "write_file":
            self.written = args["content"]
            return {}, None
        return {}, None


def test_hosts_add_preserves_existing_entries_on_good_read():
    ag = _AgOK("127.0.0.1 localhost\r\n10.0.0.9 keep.example\r\n")
    ok, _ = hosts.add(ag, "artwork.local", "192.168.122.1")
    assert ok is True
    assert "keep.example" in ag.written and "artwork.local" in ag.written


# --- bug 8: read-only broker must not run host-mutating NET_COMMANDS ----------------------------


def test_broker_readonly_rejects_net_commands():
    broker.NET_ALLOWED = set()  # the --read-only state
    resp = broker.run_virsh(["transparent-on", "192.168.122.50"])
    assert resp["rc"] == 126 and "read-only" in resp["stderr"]


def test_broker_readwrite_allows_net_commands(monkeypatch):
    broker.NET_ALLOWED = set(broker.NET_COMMANDS)
    seen = {}

    def fake_net(argv):
        seen["argv"] = argv
        return {"rc": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(broker, "run_network", fake_net)
    resp = broker.run_virsh(["transparent-on", "192.168.122.50"])
    assert resp["rc"] == 0 and seen["argv"][0] == "transparent-on"
    broker.NET_ALLOWED = set()  # restore default read-only state for other tests


# --- bug 12: guest file handle must be released even if a mid-stream op raises ------------------


def test_guest_read_file_closes_handle_on_read_error(monkeypatch):
    import guest_exec

    seen = []

    def fake_run(cmd, *a, **kw):
        payload = cmd[3] if len(cmd) > 3 else ""
        seen.append(payload)
        if '"guest-file-open"' in payload:
            return types.SimpleNamespace(stdout='{"return": 7}')
        if '"guest-file-read"' in payload:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
        return types.SimpleNamespace(stdout='{"return": {}}')

    monkeypatch.setattr(guest_exec.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        guest_exec.guest_read_file("vm", "C:/x")
    assert any('"guest-file-close"' in p for p in seen)  # handle released despite the read error


def test_guest_write_file_closes_handle_on_write_error(monkeypatch, tmp_path):
    import guest_upload

    local = tmp_path / "payload.bin"
    local.write_bytes(b"x" * 10)
    seen = []

    def fake_run(cmd, *a, **kw):
        payload = cmd[3] if len(cmd) > 3 else ""
        seen.append(payload)
        if '"guest-file-open"' in payload:
            return types.SimpleNamespace(stdout='{"return": 7}')
        if '"guest-file-write"' in payload:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
        return types.SimpleNamespace(stdout='{"return": {}}')

    monkeypatch.setattr(guest_upload.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        guest_upload.guest_write_file("vm", "C:/x", str(local))
    assert any('"guest-file-close"' in p for p in seen)
