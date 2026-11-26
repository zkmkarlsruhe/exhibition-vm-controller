"""conservation/guest_ip.py: no `--source agent` query unless the qemu-ga channel is
connected (libvirtd logs every rejected agent query as error), lease/ARP first."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cons_guest_ip", _ROOT / "conservation/guest_ip.py")
guest_ip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guest_ip)

_XML = "<domain><devices>{}</devices></domain>"
_CHAN = "<channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0'{}/></channel>"
NO_CHANNEL = _XML.format("")
DISCONNECTED = _XML.format(_CHAN.format(" state='disconnected'"))
CONNECTED = _XML.format(_CHAN.format(" state='connected'"))
AGENT_OUT = " Name  MAC  Protocol  Address\n---\n eth0  52:54:00:00:00:01  ipv4  10.0.0.5/24\n"


def _stub(monkeypatch, xml, outputs):
    calls = []

    def virsh(*args):
        calls.append(args)
        out = xml if args[0] == "dumpxml" else outputs.get(args[-1], "")
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(guest_ip, "_virsh", virsh)
    return calls


@pytest.mark.parametrize("xml", [NO_CHANNEL, DISCONNECTED])
def test_no_agent_query_without_connected_channel(monkeypatch, xml):
    calls = _stub(monkeypatch, xml, {"agent": AGENT_OUT})
    assert guest_ip.resolve_ip("vm") is None
    assert ("domifaddr", "vm", "--source", "agent") not in calls
    assert ("domifaddr", "vm", "--source", "lease") in calls


def test_agent_queried_when_connected(monkeypatch):
    calls = _stub(monkeypatch, CONNECTED, {"agent": AGENT_OUT})
    assert guest_ip.resolve_ip("vm") == "10.0.0.5"
    assert ("domifaddr", "vm", "--source", "agent") in calls


def test_lease_wins_without_dumpxml(monkeypatch):
    calls = _stub(monkeypatch, CONNECTED, {"lease": AGENT_OUT})
    assert guest_ip.resolve_ip("vm") == "10.0.0.5"
    assert not any(c[0] == "dumpxml" for c in calls)
