#!/usr/bin/env python3
"""virsh broker — the ONLY thing the conservation toolkit installs on the host.

The toolkit runs in a container (Docker, Option A: host network). It must drive
libvirt, but we don't want to hand the container the raw libvirt socket (full
control) or install libvirt-clients in it. Instead this tiny stdlib-only daemon
listens on a unix socket and forwards a **whitelisted** set of `virsh`
subcommands to the real `virsh` on the host. The container talks to it through a
drop-in `virsh` shim, so every existing script works unchanged.

Host footprint = this one file + one socket. Nothing else.

Run on the host (as a user that can reach the system VMs, e.g. in the `libvirt`
group):
    ./virsh-broker.py --socket /run/cons-virsh.sock
    ./virsh-broker.py --socket ./cons-virsh.sock --read-only   # no lifecycle/snapshot

Protocol (newline-delimited JSON per connection):
    -> {"argv": ["domstate", "CYF-Example"]}
    <- {"rc": 0, "stdout": "...", "stderr": "..."}
    screenshot returns the image inline as file_b64 (the shim writes it to the
    requested path, since virsh runs host-side and the path is the container's).

NO AUTH on the socket beyond filesystem permissions — trusted host only.
"""

import argparse
import base64
import json
import os
import socketserver
import subprocess
import sys
import tempfile

# Subcommands the toolkit needs. Mutating ones are gated behind --read-only.
READ_ONLY = {
    "list",
    "domstate",
    "domiflist",
    "domifaddr",
    "dominfo",
    "dumpxml",
    "net-dumpxml",
    "net-dhcp-leases",
    "net-list",
    "snapshot-list",
    "qemu-agent-command",  # guest-exec/file ops; can mutate the guest but not the host config
    "screenshot",
}
MUTATING = {
    "start",
    "reboot",
    "reset",
    "shutdown",
    "destroy",
    "send-key",
    "snapshot-create-as",
    "snapshot-revert",
    "snapshot-delete",
}

ALLOW = set(READ_ONLY)  # extended in main() unless --read-only

# --- transparent interception (iptables REDIRECT, via scoped sudo) ---
# These are NOT virsh; they let the (containerized) proxy turn transparent mode
# on/off per VM without touching the host beyond ephemeral, auto-cleaned rules.
NET_COMMANDS = {"transparent-on", "transparent-off", "dns-on", "dns-off"}
# NET_COMMANDS mutate host iptables (via scoped sudo), so they are host-mutating just like the
# lifecycle/snapshot virsh subcommands and must obey --read-only. Populated in main() unless
# --read-only, mirroring ALLOW.
NET_ALLOWED = set()
HOST_IP = "192.168.122.1"  # libvirt default gateway / proxy host
_ADDED = []  # [(args...)] for cleanup on exit


def _iptables(action, args):
    # sudo -n: never prompt; needs a scoped NOPASSWD sudoers rule (see deployment/)
    return subprocess.run(
        ["sudo", "-n", "iptables", "-t", "nat", action, "PREROUTING"] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _redirect_rules(vmip, dport, to_port, proto="tcp", exclude_host=True):
    r = ["-s", vmip, "-p", proto, "--dport", str(dport)]
    if exclude_host:
        r += ["!", "-d", HOST_IP]
    return r + ["-j", "REDIRECT", "--to-port", str(to_port)]


def run_network(argv):
    cmd = argv[0]
    vmip = argv[1] if len(argv) > 1 else ""
    if not vmip:
        return {"rc": 2, "stdout": "", "stderr": "need vm ip"}
    rulesets = []
    if cmd.startswith("transparent"):
        port = argv[2] if len(argv) > 2 else "8080"
        rulesets = [_redirect_rules(vmip, 80, port), _redirect_rules(vmip, 443, port)]
    else:  # dns
        port = argv[2] if len(argv) > 2 else "5354"
        rulesets = [
            _redirect_rules(vmip, 53, port, "udp", exclude_host=False),
            _redirect_rules(vmip, 53, port, "tcp", exclude_host=False),
        ]
    on = cmd.endswith("-on")
    errs = []
    for rule in rulesets:
        _iptables("-D", rule)  # idempotent: drop any stale copy first
        if on:
            p = _iptables("-A", rule)
            if p.returncode != 0:
                errs.append(p.stderr.strip())
            else:
                _ADDED.append(rule)
        else:
            _ADDED[:] = [r for r in _ADDED if r != rule]
    if errs:
        return {
            "rc": 1,
            "stdout": "",
            "stderr": "; ".join(errs)
            + " (need the iptables NOPASSWD sudoers rule — see deployment/)",
        }
    return {"rc": 0, "stdout": "%s ok for %s" % (cmd, vmip), "stderr": ""}


def _cleanup_rules():
    for rule in list(_ADDED):
        _iptables("-D", rule)
    _ADDED.clear()


def run_virsh(argv):
    """Run `virsh <argv>` host-side. Special-case screenshot to return bytes."""
    if not argv:
        return {"rc": 2, "stdout": "", "stderr": "empty argv"}
    sub = argv[0]
    if sub in NET_COMMANDS:
        if sub not in NET_ALLOWED:
            return {
                "rc": 126,
                "stdout": "",
                "stderr": "subcommand not permitted by broker (read-only): " + sub,
            }
        return run_network(argv)
    if sub not in ALLOW:
        return {"rc": 126, "stdout": "", "stderr": "subcommand not permitted by broker: " + sub}

    if sub == "screenshot":
        # argv = ["screenshot", VM, <container-path>]; run to a HOST temp file,
        # return the bytes — the shim writes them to the container path.
        # mkstemp() creates the file securely (O_EXCL) — mktemp() only returned a name, leaving a
        # symlink race between the name being handed out and virsh writing to it.
        fd, host_tmp = tempfile.mkstemp(suffix=".ppm")
        os.close(fd)
        vm = argv[1] if len(argv) > 1 else ""
        p = subprocess.run(
            ["virsh", "screenshot", vm, host_tmp], capture_output=True, text=True, timeout=30
        )
        data = b""
        try:
            with open(host_tmp, "rb") as fh:
                data = fh.read()
        finally:
            if os.path.exists(host_tmp):
                os.remove(host_tmp)
        return {
            "rc": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
            "file_b64": base64.b64encode(data).decode("ascii"),
        }

    p = subprocess.run(["virsh", *argv], capture_output=True, text=True, timeout=120)
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = run_virsh(req.get("argv", []))
            except Exception as e:  # never crash the broker on a bad request
                resp = {"rc": 1, "stdout": "", "stderr": "broker error: " + repr(e)}
            self.wfile.write((json.dumps(resp) + "\n").encode())
            self.wfile.flush()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global ALLOW, NET_ALLOWED
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--socket",
        default="/tmp/cons-sock/virsh.sock",
        help="unix socket path (its dir is bind-mounted into the container)",
    )
    ap.add_argument(
        "--read-only",
        action="store_true",
        help="forbid lifecycle/snapshot/send-key (introspection only)",
    )
    args = ap.parse_args()
    if not args.read_only:
        ALLOW |= MUTATING
        NET_ALLOWED |= NET_COMMANDS

    os.makedirs(os.path.dirname(args.socket) or ".", exist_ok=True)
    if os.path.exists(args.socket):
        os.remove(args.socket)
    server = Server(args.socket, Handler)
    os.chmod(args.socket, 0o660)  # owner+group; mount into the container
    sys.stderr.write(
        "virsh-broker listening on %s  (allow: %d subcommands%s)\n"
        % (args.socket, len(ALLOW), ", read-only" if args.read_only else "")
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_rules()  # remove any transparent/dns iptables rules we added
        if os.path.exists(args.socket):
            os.remove(args.socket)


if __name__ == "__main__":
    main()
