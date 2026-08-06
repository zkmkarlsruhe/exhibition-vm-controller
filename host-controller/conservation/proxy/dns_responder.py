#!/usr/bin/env python3
"""Tiny DNS responder for transparent conservation — dead hosts -> the proxy.

Used in transparent mode for hosts whose DNS is *gone* (NXDOMAIN), so the guest
can't even open a socket. The broker redirects the guest's :53 here; we answer
matched dead hostnames with the proxy IP (then the :80/:443 iptables REDIRECT
catches the connection), and forward everything else to a real upstream so the
guest's normal name resolution keeps working.

Stdlib only — no dnsmasq, no deps.

    dns_responder.py --to 192.168.122.1 --host www.dead-server.com --host '*.sounddogs.com'
    dns_responder.py --to 192.168.122.1 --catch-all          # answer EVERYTHING (careful)

NO AUTH — trusted museum LAN only.
"""

import argparse
import socket
import struct
import sys
import threading

TO_IP = "192.168.122.1"
HOSTS = []  # lowercased exact names and '*.suffix' wildcards
CATCH_ALL = False
UPSTREAM = ("8.8.8.8", 53)


def matches(name):
    if CATCH_ALL:
        return True
    name = name.lower().rstrip(".")
    for h in HOSTS:
        if h.startswith("*."):
            if name == h[2:] or name.endswith(h[1:]):  # *.x matches x and *.x
                return True
        elif name == h:
            return True
    return False


def parse_qname(data, off):
    labels = []
    while True:
        n = data[off]
        if n == 0:
            off += 1
            break
        labels.append(data[off + 1 : off + 1 + n].decode("latin-1"))
        off += 1 + n
    return ".".join(labels), off


def build_a_response(query):
    # header: id, flags=0x8180 (resp+RA), qd=1, an=1, ns=0, ar=0
    qid = query[:2]
    qname, qend = parse_qname(query, 12)
    qtype, qclass = struct.unpack_from(">HH", query, qend)
    question = query[12 : qend + 4]
    header = qid + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0)
    answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton(TO_IP)
    return header + question + answer, qname, qtype


def forward(query):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(4)
        s.sendto(query, UPSTREAM)
        resp, _ = s.recvfrom(4096)
        s.close()
        return resp
    except OSError:
        return None


def handle(sock, data, addr):
    try:
        qname, qend = parse_qname(data, 12)
        qtype = struct.unpack_from(">H", data, qend)[0]
    except (IndexError, struct.error):
        return
    if qtype == 1 and matches(qname):  # A query for a dead host
        resp, name, _ = build_a_response(data)
        sys.stderr.write("[dns] %s -> %s (conserved)\n" % (qname, TO_IP))
        sys.stderr.flush()
        sock.sendto(resp, addr)
    else:
        resp = forward(data)
        if resp:
            sock.sendto(resp, addr)


def main():
    global TO_IP, HOSTS, CATCH_ALL, UPSTREAM
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5354)
    ap.add_argument(
        "--to", default="192.168.122.1", help="IP to answer matched hosts with (the proxy)"
    )
    ap.add_argument(
        "--host",
        action="append",
        default=[],
        help="dead host to redirect (repeatable; '*.x' wildcard)",
    )
    ap.add_argument(
        "--catch-all", action="store_true", help="answer EVERYTHING with --to (use with care)"
    )
    ap.add_argument("--upstream", default="8.8.8.8:53")
    args = ap.parse_args()
    TO_IP = args.to
    HOSTS = [h.lower().rstrip(".") for h in args.host]
    CATCH_ALL = args.catch_all
    uh, _, up = args.upstream.partition(":")
    UPSTREAM = (uh, int(up or 53))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.listen, args.port))
    sys.stderr.write(
        "dns-responder on %s:%d  -> %s for %s; else forward %s\n"
        % (args.listen, args.port, TO_IP, "ALL" if CATCH_ALL else (HOSTS or "(none)"), UPSTREAM)
    )
    sys.stderr.flush()
    while True:
        data, addr = sock.recvfrom(4096)
        threading.Thread(target=handle, args=(sock, data, addr), daemon=True).start()


if __name__ == "__main__":
    main()
