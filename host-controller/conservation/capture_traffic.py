#!/usr/bin/env python3
"""Capture and summarize ALL network traffic from an exhibition VM.

Phase-0 conservation tool. Before proxying or stubbing anything, you need the
complete, protocol-agnostic inventory of what an artwork actually reaches for
on the network.

Ground truth is tcpdump on the VM's *own tap interface* (vnetN). That tap sits
below the guest, so it sees every packet the guest emits — all ports, TCP and
UDP, encrypted or not — and only that one VM's traffic (no cross-talk from the
other artworks on the bridge).

The summary then surfaces the dead-host candidates:
  - DNS lookups        (what names it tried to resolve)
  - TLS SNI hostnames  (HTTPS targets, readable without decrypting)
  - HTTP Host + path    (plain-HTTP targets)
  - destination IP:port with a protocol breakdown, so non-HTTP flows
    (RTMP 1935, RTSP 554, raw sockets, …) are obvious and not silently missed

Traffic to the host/controller itself (heartbeat, API) is detected and shown
separately so it doesn't drown out the external reach.

Usage:
    sudo ./capture_traffic.py <vm-name> [--seconds N] [--out FILE] [--iface IF]

Examples:
    sudo ./capture_traffic.py CYF-Subfusion --seconds 120
    sudo ./capture_traffic.py "CYF-Example" --out /tmp/capture.pcap
    # or omit --seconds and stop with Ctrl-C; the summary prints on exit

Requires: tcpdump (capture + fallback summary).
Optional:  tshark — when present, adds SNI / HTTP-Host / DNS-name extraction.
The raw .pcap is always written so you can open it in Wireshark afterwards.
"""

import argparse
import ipaddress
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter


def _virsh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["virsh", *args], capture_output=True, text=True)


def resolve_interfaces(vm_name: str) -> tuple[list[str], str | None]:
    """Return ([tap interface names], host_ip) for the VM via libvirt.

    host_ip is the gateway/controller address on the VM's network, used to
    separate heartbeat/API noise from real external traffic.
    """
    r = _virsh("domiflist", vm_name)
    if r.returncode != 0:
        raise ValueError(f"virsh domiflist failed for '{vm_name}': {r.stderr.strip()}")

    taps: list[str] = []
    network_name: str | None = None
    for line in r.stdout.splitlines()[2:]:  # skip header + rule line
        parts = line.split()
        if len(parts) >= 3 and parts[0] not in ("-", ""):
            taps.append(parts[0])
            if parts[1] == "network" and network_name is None:
                network_name = parts[2]

    if not taps:
        raise ValueError(
            f"no active tap interface for '{vm_name}'. Is the VM running? (virsh start it first)"
        )

    host_ip = _resolve_host_ip(network_name) if network_name else None
    return taps, host_ip


def _resolve_host_ip(network_name: str) -> str | None:
    r = _virsh("net-dumpxml", network_name)
    if r.returncode != 0:
        return None
    try:
        ip_elem = ET.fromstring(r.stdout).find(".//ip")
        return ip_elem.get("address") if ip_elem is not None else None
    except ET.ParseError:
        return None


def capture(iface: str, pcap_path: str, seconds: int | None) -> None:
    cmd = ["tcpdump", "-i", iface, "-nn", "-U", "-w", pcap_path]
    how = f"{seconds}s" if seconds else "until Ctrl-C"
    print(f"==> capturing on {iface} -> {pcap_path} ({how})", file=sys.stderr)
    print("    interact with the artwork now so it makes its network calls\n", file=sys.stderr)

    proc = subprocess.Popen(cmd)
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGINT)
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()
    print(file=sys.stderr)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------


def _tshark_field(pcap: str, display_filter: str, *fields: str) -> list[str]:
    cmd = ["tshark", "-r", pcap, "-Y", display_filter, "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


# --------------------------------------------------------------------------
# Structured analysis — shared by the CLI and the web UI.
# analyze() returns a plain dict (JSON-serializable) so callers can render it
# however they like.
# --------------------------------------------------------------------------


def _collect_tshark(pcap: str) -> dict[str, Counter]:
    dns = Counter(_tshark_field(pcap, "dns.flags.response==0", "dns.qry.name"))

    sni: Counter = Counter()
    for line in _tshark_field(
        pcap, "tls.handshake.extensions_server_name", "tls.handshake.extensions_server_name"
    ):
        for name in line.split(","):
            if name.strip():
                sni[name.strip()] += 1

    http: Counter = Counter()
    for line in _tshark_field(
        pcap, "http.request", "http.host", "http.request.method", "http.request.uri"
    ):
        cols = line.split("\t")
        host = cols[0] if len(cols) > 0 else ""
        method = cols[1] if len(cols) > 1 else ""
        uri = cols[2] if len(cols) > 2 else ""
        http[f"{method} http://{host}{uri}"] += 1

    dests: Counter = Counter()
    for proto, port_field in (("TCP", "tcp.dstport"), ("UDP", "udp.dstport")):
        for line in _tshark_field(pcap, proto.lower(), "ip.dst", port_field):
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0] and cols[1]:
                dests[(cols[0], cols[1], proto)] += 1

    return {"dns": dns, "sni": sni, "http": http, "dests": dests}


def _collect_tcpdump(pcap: str) -> dict[str, Counter]:
    dns: Counter = Counter()
    r = subprocess.run(
        ["tcpdump", "-nn", "-r", pcap, "udp port 53"], capture_output=True, text=True
    )
    for line in r.stdout.splitlines():
        # tcpdump renders queries as: ... 12345+ A? example.com. (33)
        if "?" in line:
            after = line.split("?", 1)[1].split()
            if after:
                name = after[0].rstrip(".")
                if name:
                    dns[name] += 1

    dests: Counter = Counter()
    r = subprocess.run(["tcpdump", "-nn", "-q", "-r", pcap], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        # ... IP src.sport > dst.dport: tcp ...
        if " > " not in line:
            continue
        proto = "UDP" if " UDP" in line else "TCP"
        try:
            dst = line.split(" > ")[1].split(":")[0]
            ip, _, port = dst.rpartition(".")
            if ip and port.isdigit():
                dests[(ip, port, proto)] += 1
        except (IndexError, ValueError):
            continue

    return {"dns": dns, "sni": Counter(), "http": Counter(), "dests": dests}


def analyze(pcap: str, host_ip: str | None) -> dict:
    """Return a JSON-serializable summary of the pcap.

    Uses tshark for SNI/HTTP/DNS field extraction when available, otherwise a
    tcpdump-only fallback (destinations + DNS names).
    """
    if shutil.which("tshark"):
        tool, collected = "tshark", _collect_tshark(pcap)
    else:
        tool, collected = "tcpdump", _collect_tcpdump(pcap)

    external, hostbound = [], []
    for (ip, port, proto), count in collected["dests"].most_common():
        entry = {"ip": ip, "port": port, "proto": proto, "count": count}
        if host_ip and ip == host_ip:
            hostbound.append(entry)
        else:
            external.append({**entry, "hint": _port_hint(port, proto).strip()})

    def pairs(counter: Counter) -> list[dict]:
        return [{"value": v, "count": c} for v, c in counter.most_common()]

    return {
        "tool": tool,
        "host_ip": host_ip,
        "dns": pairs(collected["dns"]),
        "sni": pairs(collected["sni"]),
        "http": pairs(collected["http"]),
        "external": external,
        "hostbound": hostbound,
    }


def print_summary(data: dict) -> None:
    if data["tool"] == "tcpdump":
        print(
            "(tshark not installed — basic summary from tcpdump; "
            "install wireshark/tshark for SNI + HTTP Host extraction)\n"
        )

    def block(title: str, items: list[dict], limit: int = 30) -> None:
        print(f"=== {title} ===")
        if not items:
            print("  (none)")
        for it in items[:limit]:
            print(f"  {it['count']:5d}  {it['value']}")
        if len(items) > limit:
            print(f"  … +{len(items) - limit} more")
        print()

    block("DNS lookups (names the artwork tried to resolve)", data["dns"])
    block("TLS SNI (HTTPS targets — readable without decrypting)", data["sni"])
    block("HTTP requests (plain-HTTP targets)", data["http"], limit=40)

    print("=== Destinations (IP:port, protocol breakdown) ===")
    print("  EXTERNAL  (proxy/stub candidates):")
    if not data["external"]:
        print("    (none)")
    for d in data["external"][:40]:
        hint = f"  {d['hint']}" if d.get("hint") else ""
        print(f"    {d['count']:5d}  {d['ip']}:{d['port']}/{d['proto']}{hint}")
    if data["hostbound"]:
        print(f"\n  HOST/CONTROLLER ({data['host_ip']} — heartbeat/API, ignore):")
        for d in data["hostbound"][:10]:
            print(f"    {d['count']:5d}  {d['ip']}:{d['port']}/{d['proto']}")
    if data["tool"] == "tcpdump":
        print(
            "\nnote: HTTPS SNI and HTTP Host names need tshark; the raw .pcap "
            "has everything — open it in Wireshark for full detail."
        )


def _port_hint(port: str, proto: str) -> str:
    hints = {
        "80": "  http",
        "443": "  https",
        "53": "  dns",
        "1935": "  RTMP (Flash media — NOT http, needs separate handling)",
        "554": "  RTSP (streaming — NOT http)",
        "21": "  FTP",
        "1755": "  MMS (Windows Media)",
        "8080": "  http-alt",
        "8000": "  http-alt",
    }
    h = hints.get(port, "")
    if not h and proto == "TCP" and port not in ("80", "443"):
        h = "  (non-standard — check protocol)"
    return h


def main() -> None:
    p = argparse.ArgumentParser(
        description="Capture and summarize all network traffic from an exhibition VM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("vm_name", help="libvirt VM name (e.g. CYF-Subfusion)")
    p.add_argument(
        "--seconds", "-s", type=int, default=None, help="capture duration; omit to run until Ctrl-C"
    )
    p.add_argument(
        "--out", "-o", default=None, help="pcap output path (default: ./<vm>-traffic.pcap)"
    )
    p.add_argument(
        "--iface", "-i", default=None, help="override capture interface (default: the VM's tap)"
    )
    args = p.parse_args()

    if os.geteuid() != 0:
        sys.exit("error: packet capture needs root — re-run with sudo")
    if not shutil.which("tcpdump"):
        sys.exit("error: tcpdump not found — install it (apt install tcpdump)")

    try:
        taps, host_ip = resolve_interfaces(args.vm_name)
    except ValueError as e:
        sys.exit(f"error: {e}")
    iface = args.iface or taps[0]
    if not args.iface and len(taps) > 1:
        print(
            f"note: VM has multiple taps {taps}; capturing on {iface}. "
            f"Use --iface to pick another.",
            file=sys.stderr,
        )

    safe = args.vm_name.replace(" ", "_").replace("/", "_")
    pcap_path = args.out or os.path.join(os.getcwd(), f"{safe}-traffic.pcap")

    capture(iface, pcap_path, args.seconds)

    print("=" * 70)
    print(f"TRAFFIC SUMMARY for '{args.vm_name}'  (host/controller IP: {host_ip or 'unknown'})")
    print("=" * 70 + "\n")
    print_summary(analyze(pcap_path, host_ip))
    print(f"\nraw capture saved: {pcap_path}  (open in Wireshark for full detail)")


if __name__ == "__main__":
    main()
