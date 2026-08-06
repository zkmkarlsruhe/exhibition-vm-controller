#!/usr/bin/env python3
"""Install mitmproxy's CA into a guest so the artwork trusts the proxy (HTTPS).

Reads mitmproxy's generated CA and calls the in-guest agent's `install_cert`
tool over the bridge — a silent registry-blob write, no prompt. Run it once and
bake it into the `ready` snapshot.

    install-ca --vm "CYF-Example"
    install-ca --agent 192.168.122.215:9009 --ca /cadir/mitmproxy-ca-cert.pem
"""

import argparse
import json
import socket
import subprocess
import sys


def resolve_agent_ip(vm):
    r = subprocess.run(
        ["virsh", "domifaddr", vm, "--source", "agent"], capture_output=True, text=True, timeout=20
    )
    for line in r.stdout.splitlines():
        for tok in line.split():
            if tok.count(".") == 3 and not tok.startswith("127."):
                return tok.split("/")[0]
    return None


def agent_call(ip, port, name, args):
    s = socket.create_connection((ip, port), timeout=15)
    f = s.makefile("rwb")

    def call(o):
        f.write((json.dumps(o) + "\n").encode())
        f.flush()
        return json.loads(f.readline())

    call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    r = call(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )
    s.close()
    return r["result"]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--vm", help="VM name (auto-resolve the guest agent IP)")
    ap.add_argument("--agent", help="guest agent ip:port (instead of --vm)")
    ap.add_argument("--ca", default="/cadir/mitmproxy-ca-cert.pem", help="CA PEM path")
    ap.add_argument("--scope", default="user", help="user (default) or machine")
    args = ap.parse_args()

    try:
        pem = open(args.ca).read()
    except OSError as e:
        sys.exit("cannot read CA %s: %s (run the proxy once to generate it)" % (args.ca, e))

    if args.agent:
        ip, _, port = args.agent.partition(":")
        port = int(port or 9009)
    elif args.vm:
        ip = resolve_agent_ip(args.vm)
        port = 9009
        if not ip:
            sys.exit("could not resolve guest agent IP for %r (VM off? agent down?)" % args.vm)
    else:
        sys.exit("need --vm or --agent")

    print("installing mitmproxy CA into %s:%d (scope=%s)..." % (ip, port, args.scope))
    res = agent_call(ip, port, "install_cert", {"pem": pem, "scope": args.scope})
    print(res["content"][0]["text"])
    sys.exit(1 if res.get("isError") else 0)


if __name__ == "__main__":
    main()
