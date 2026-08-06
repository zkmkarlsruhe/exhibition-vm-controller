#!/usr/bin/env python3
"""Install the proxy CA into ALL of a guest's trust stores, over the agent.

Different runtimes keep their own trust store, so one install isn't enough:

  * Windows root  — IE / WinINet / SChannel / Flash / Director. Done with the
    agent's silent `install_cert` (registry blob).
  * Java cacerts  — every JRE has its own store. `keytool` ships inside the JRE,
    so we just run it against each `cacerts` we find (default pw "changeit").
  * NSS (Firefox / Mozilla) — its own cert db, ignores the Windows store. We
    enable `security.enterprise_roots` per profile so Firefox 49+ inherits the
    Windows root store (where we already put the CA). No NSS tools to ship.

    Caveat: pre-49 / ancient Mozilla has no enterprise-roots pref — those need
    the NSS `certutil` against cert8.db (not shipped here); flagged when found.

    install-trust --vm "CYF-Example" --ca /cadir/mitmproxy-ca-cert.pem
    install-trust --agent 192.168.122.215:9009 --stores java,nss
"""

import argparse
import json
import ntpath
import socket
import subprocess
import sys

GUEST_CA = r"C:\zkm-proxy-ca.pem"
ALIAS = "zkm-proxy"


def resolve_agent_ip(vm):
    r = subprocess.run(
        ["virsh", "domifaddr", vm, "--source", "agent"], capture_output=True, text=True, timeout=20
    )
    for line in r.stdout.splitlines():
        for tok in line.split():
            if tok.count(".") == 3 and not tok.startswith("127."):
                return tok.split("/")[0]
    return None


class Agent:
    def __init__(self, ip, port):
        self.ip, self.port = ip, port

    def call(self, name, args, tmo=40):
        s = socket.create_connection((self.ip, self.port), timeout=tmo)
        f = s.makefile("rwb")
        f.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n").encode())
        f.flush()
        f.readline()
        f.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args},
                    }
                )
                + "\n"
            ).encode()
        )
        f.flush()
        r = json.loads(f.readline())
        s.close()
        res = r["result"]
        txt = res["content"][0]["text"]
        try:
            return json.loads(txt), res.get("isError", False)
        except ValueError:
            return txt, res.get("isError", False)

    def sh(self, cmd):
        out, _ = self.call("run_shell", {"command": cmd})
        return out if isinstance(out, dict) else {"stdout": "", "stderr": out, "exit_code": -1}

    def find(self, d, pat):
        out, _ = self.call("find_files", {"dir": d, "pattern": pat, "limit": 50})
        return out.get("files", []) if isinstance(out, dict) else []


def do_windows(ag, pem, scope):
    res, err = ag.call("install_cert", {"pem": pem, "scope": scope})
    ok = isinstance(res, dict) and res.get("installed")
    print(
        "  Windows root : %s"
        % ("OK (thumbprint %s)" % res.get("thumbprint") if ok else "FAILED %s" % res)
    )


def do_java(ag):
    bases = [
        r"C:\Program Files\Java",
        r"C:\Program Files (x86)\Java",
        r"C:\Program Files\Eclipse Adoptium",
        r"C:\Program Files\Zulu",
    ]
    cacerts = []
    for b in bases:
        cacerts += ag.find(b, "cacerts")
    cacerts = sorted(set(cacerts))
    if not cacerts:
        print("  Java         : no JRE/cacerts found — skipped")
        return
    for cc in cacerts:
        jre = ntpath.dirname(ntpath.dirname(ntpath.dirname(cc)))  # ...\lib\security\cacerts -> JRE
        keytool = jre + r"\bin\keytool.exe"
        cmd = (
            '"%s" -importcert -noprompt -trustcacerts -alias %s -file %s -keystore "%s" -storepass changeit'
            % (keytool, ALIAS, GUEST_CA, cc)
        )
        r = ag.sh(cmd)
        msg = (r.get("stderr") or r.get("stdout") or "").strip().splitlines()
        ok = r.get("exit_code") == 0
        print("  Java cacerts : %s  %s" % ("OK" if ok else "FAILED", cc))
        if not ok and msg:
            print("                 %s" % msg[0])


def do_nss(ag):
    env, _ = ag.call("env", {})
    appdata = env.get("APPDATA", "") if isinstance(env, dict) else ""
    roots = []
    if appdata:
        roots += [
            appdata + r"\Mozilla\Firefox\Profiles",
            appdata + r"\Mozilla\SeaMonkey\Profiles",
            appdata + r"\Thunderbird\Profiles",
        ]
    profiles = set()
    for r in roots:
        for db in ("cert9.db", "cert8.db"):
            for hit in ag.find(r, db):
                profiles.add(ntpath.dirname(hit))
    if not profiles:
        print("  NSS/Firefox  : no Mozilla profiles found — skipped")
        return
    PREF = 'user_pref("security.enterprise_roots.enabled", true);'
    for prof in sorted(profiles):
        userjs = prof + r"\user.js"
        cur, _ = ag.call("read_file", {"path": userjs})
        body = cur.get("text", "") if isinstance(cur, dict) else ""
        if PREF in body:
            print("  NSS profile  : already set  %s" % prof)
            continue
        new = body + ("\r\n" if body and not body.endswith("\n") else "") + PREF + "\r\n"
        res, err = ag.call("write_file", {"path": userjs, "content": new})
        print("  NSS profile  : %s (enterprise_roots) %s" % ("OK" if not err else "FAILED", prof))
        print("                 note: needs Firefox 49+; older Mozilla needs NSS certutil")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--vm")
    ap.add_argument("--agent")
    ap.add_argument("--ca", default="/cadir/mitmproxy-ca-cert.pem")
    ap.add_argument("--stores", default="windows,java,nss", help="comma list of: windows,java,nss")
    ap.add_argument("--scope", default="user")
    args = ap.parse_args()

    try:
        pem = open(args.ca).read()
    except OSError as e:
        sys.exit("cannot read CA %s: %s" % (args.ca, e))

    if args.agent:
        ip, _, port = args.agent.partition(":")
        port = int(port or 9009)
    elif args.vm:
        ip = resolve_agent_ip(args.vm)
        port = 9009
        if not ip:
            sys.exit("could not resolve guest agent IP for %r" % args.vm)
    else:
        sys.exit("need --vm or --agent")

    ag = Agent(ip, port)
    # stage the CA in the guest for keytool -file
    ag.call("write_file", {"path": GUEST_CA, "content": pem})
    stores = [s.strip() for s in args.stores.split(",") if s.strip()]
    print("installing CA into %s on %s:%d" % ("+".join(stores), ip, port))
    if "windows" in stores:
        do_windows(ag, pem, args.scope)
    if "java" in stores:
        do_java(ag)
    if "nss" in stores:
        do_nss(ag)


if __name__ == "__main__":
    main()
