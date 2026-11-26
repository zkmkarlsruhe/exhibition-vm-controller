"""Guest IPv4 via libvirt, without tripping libvirt's error log.

`virsh domifaddr --source agent` on a domain without a qemu-ga channel, or with the
channel disconnected, fails and libvirtd logs it as an error ("QEMU guest agent is not
configured" / "... is not connected"). Ask DHCP lease and ARP first; ask the agent only
when the domain XML shows its channel connected — the same pre-check libvirt does.
Stdlib only: runs on the host and in the conservation container (virsh shim/broker).
"""

import subprocess
import xml.etree.ElementTree as ET

QGA_CHANNEL = "org.qemu.guest_agent.0"


def _virsh(*args):
    return subprocess.run(["virsh", *args], capture_output=True, text=True, timeout=20)


def agent_available(vm):
    """True when the qemu-ga channel exists and is not 'disconnected'."""
    try:
        root = ET.fromstring(_virsh("dumpxml", vm).stdout)
    except ET.ParseError:
        return False
    for t in root.findall("./devices/channel/target"):
        if t.get("name") == QGA_CHANNEL:
            return t.get("state") != "disconnected"
    return False


def resolve_ip(vm):
    """First non-loopback IPv4 of `vm` (lease, ARP, then agent if connected), or None."""
    for src in ("lease", "arp", "agent"):
        if src == "agent" and not agent_available(vm):
            break
        for line in _virsh("domifaddr", vm, "--source", src).stdout.splitlines():
            for tok in line.split():
                if tok.count(".") == 3 and not tok.startswith("127."):
                    return tok.split("/")[0]
    return None
