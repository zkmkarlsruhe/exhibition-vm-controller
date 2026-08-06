#!/usr/bin/env python3
"""Host-side MCP server — debug an artwork VM from OUTSIDE, and bridge INSIDE.

One MCP endpoint that an AI agent uses to work on a legacy artwork end to end:

  * HOST / hypervisor tools (libvirt + QEMU) — work even on a dead or agentless
    guest: lifecycle, snapshots, framebuffer screenshot, key injection, XML/net
    introspection, and a raw `virsh` escape hatch.
  * GUEST tools — every tool the in-guest agent (legacy_agent / legacy-agent.exe)
    exposes is fetched live and re-published here as `guest_<name>`, forwarded
    over the TCP bridge. So `guest_screenshot`, `guest_window_controls`,
    `guest_process_modules`, … appear right next to the host tools.

Together: the agent can revert a snapshot, send a key to a frozen dialog, watch
the framebuffer, AND walk the guest's process/window/registry internals — all
through a single connection.

Usage (MCP over stdio — add to Claude Code):
    claude mcp add legacy-host -- python3 host_mcp.py --vm "CYF-Example"

The guest agent IP is auto-resolved via libvirt; override with
    --agent 192.168.122.215:9009

NO AUTH — trusted museum LAN only.
"""

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile

VM = ""
AGENT_ADDR = None  # (ip, port) or None to auto-resolve


# --------------------------------------------------------------------------
# host (libvirt / qemu) helpers
# --------------------------------------------------------------------------


def virsh(*args, timeout=30):
    r = subprocess.run(["virsh", *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def resolve_agent():
    """(ip, port) of the in-guest agent — explicit override or libvirt lookup."""
    global AGENT_ADDR
    if AGENT_ADDR:
        return AGENT_ADDR
    rc, out, _ = virsh("domifaddr", VM, "--source", "agent")
    for line in out.splitlines():
        for tok in line.split():
            if tok.count(".") == 3 and not tok.startswith("127."):
                ip = tok.split("/")[0]
                AGENT_ADDR = (ip, 9009)
                return AGENT_ADDR
    return None


# --------------------------------------------------------------------------
# guest bridge — speak MCP to the in-guest agent over TCP
# --------------------------------------------------------------------------


def guest_rpc(method, params=None, timeout=30):
    addr = resolve_agent()
    if not addr:
        raise RuntimeError("guest agent address unknown (VM off, or no DHCP lease)")
    s = socket.create_connection(addr, timeout=timeout)
    try:
        f = s.makefile("rwb")
        f.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
                + "\n"
            ).encode()
        )
        f.flush()
        return json.loads(f.readline())
    finally:
        s.close()


def guest_tools():
    """Fetch the in-guest tool list; return [] if the agent isn't reachable."""
    try:
        return guest_rpc("tools/list").get("result", {}).get("tools", [])
    except Exception:
        return []


# --------------------------------------------------------------------------
# host tool handlers
# --------------------------------------------------------------------------


def h_vm_list(a):
    rc, out, _ = virsh("list", "--all")
    return {"domains": out}


def h_vm_state(a):
    rc, out, err = virsh("domstate", VM)
    return {"vm": VM, "state": out or err}


def h_vm_lifecycle(action):
    def fn(a):
        rc, out, err = virsh(action, VM)
        return {"vm": VM, "action": action, "ok": rc == 0, "message": out or err}

    return fn


def h_vm_screenshot(a):
    path = tempfile.mktemp(suffix=".ppm")
    rc, out, err = virsh("screenshot", VM, path)
    if rc != 0 or not os.path.exists(path):
        return {"__error__": "screenshot failed: " + (err or out)}
    data = open(path, "rb").read()
    os.remove(path)
    return {"__image__": base64.b64encode(data).decode("ascii"), "__mime__": "image/png"}


def h_vm_send_key(a):
    keys = a.get("keys") or []
    if isinstance(keys, str):
        keys = keys.split()
    if not keys:
        return {"__error__": "keys required (e.g. ['KEY_ENTER'] or ['KEY_LEFTALT','KEY_F4'])"}
    rc, out, err = virsh("send-key", VM, *[str(k) for k in keys])
    return {"vm": VM, "keys": keys, "ok": rc == 0, "message": out or err}


def h_vm_snapshot_list(a):
    rc, out, _ = virsh("snapshot-list", VM, "--name")
    return {"vm": VM, "snapshots": [s for s in out.splitlines() if s.strip()]}


def h_vm_snapshot(action):
    def fn(a):
        name = a.get("name", "")
        if action == "create":
            rc, out, err = (
                virsh("snapshot-create-as", VM, name) if name else virsh("snapshot-create-as", VM)
            )
        elif action == "revert":
            rc, out, err = virsh("snapshot-revert", VM, name)
        elif action == "delete":
            rc, out, err = virsh("snapshot-delete", VM, name)
        return {
            "vm": VM,
            "action": "snapshot-" + action,
            "name": name,
            "ok": rc == 0,
            "message": out or err,
        }

    return fn


def h_vm_dumpxml(a):
    rc, out, err = virsh("dumpxml", VM)
    return {"vm": VM, "xml": out or err}


def h_vm_net_info(a):
    rc, out, err = virsh("domifaddr", VM, "--source", "agent")
    return {"vm": VM, "interfaces": out or err, "agent_addr": resolve_agent()}


def h_vm_virsh(a):
    args = a.get("args") or []
    if isinstance(args, str):
        args = args.split()
    rc, out, err = virsh(*[str(x) for x in args])
    return {"args": args, "exit": rc, "stdout": out, "stderr": err}


def _obj(props, req=None):
    return {"type": "object", "properties": props or {}, "required": req or []}


HOST_TOOLS = {
    "vm_list": ("List all libvirt domains and their state.", _obj({}), h_vm_list),
    "vm_state": ("Current run state of the target VM.", _obj({}), h_vm_state),
    "vm_start": ("Start the VM.", _obj({}), h_vm_lifecycle("start")),
    "vm_reboot": ("ACPI reboot the VM.", _obj({}), h_vm_lifecycle("reboot")),
    "vm_reset": ("Hard reset the VM.", _obj({}), h_vm_lifecycle("reset")),
    "vm_shutdown": ("ACPI shutdown the VM.", _obj({}), h_vm_lifecycle("shutdown")),
    "vm_screenshot": (
        "Framebuffer screenshot via the hypervisor — works with NO guest cooperation (any OS).",
        _obj({}),
        h_vm_screenshot,
    ),
    "vm_send_key": (
        "Inject keystrokes at the hypervisor (qcode/KEY_* names), e.g. drive a frozen dialog.",
        _obj({"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"]),
        h_vm_send_key,
    ),
    "vm_snapshot_list": ("List the VM's snapshots.", _obj({}), h_vm_snapshot_list),
    "vm_snapshot_create": (
        "Create/overwrite a snapshot.",
        _obj({"name": {"type": "string"}}),
        h_vm_snapshot("create"),
    ),
    "vm_snapshot_revert": (
        "Revert the VM to a snapshot.",
        _obj({"name": {"type": "string"}}, ["name"]),
        h_vm_snapshot("revert"),
    ),
    "vm_snapshot_delete": (
        "Delete a snapshot.",
        _obj({"name": {"type": "string"}}, ["name"]),
        h_vm_snapshot("delete"),
    ),
    "vm_dumpxml": ("The VM's libvirt domain XML (devices, disks, net).", _obj({}), h_vm_dumpxml),
    "vm_net_info": (
        "Guest network interfaces / IP (via the guest agent).",
        _obj({}),
        h_vm_net_info,
    ),
    "vm_virsh": (
        "Escape hatch: run an arbitrary virsh subcommand against the host.",
        _obj({"args": {"type": "array", "items": {"type": "string"}}}, ["args"]),
        h_vm_virsh,
    ),
}


# --------------------------------------------------------------------------
# MCP protocol
# --------------------------------------------------------------------------


def make_result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def tools_list():
    out = []
    for name, (desc, schema, _h) in sorted(HOST_TOOLS.items()):
        out.append({"name": name, "description": "[host] " + desc, "inputSchema": schema})
    for t in guest_tools():
        out.append(
            {
                "name": "guest_" + t["name"],
                "description": "[guest] " + t.get("description", ""),
                "inputSchema": t.get("inputSchema", _obj({})),
            }
        )
    return {"tools": out}


def call_tool(name, args):
    if name.startswith("guest_"):
        # forward to the in-guest agent verbatim and pass its result straight back
        try:
            r = guest_rpc("tools/call", {"name": name[len("guest_") :], "arguments": args})
            return r.get(
                "result", {"content": [{"type": "text", "text": "no result"}], "isError": True}
            )
        except Exception as e:
            return {
                "content": [{"type": "text", "text": "guest bridge error: " + str(e)}],
                "isError": True,
            }
    if name in HOST_TOOLS:
        try:
            val = HOST_TOOLS[name][2](args or {})
        except Exception as e:
            return {
                "content": [{"type": "text", "text": "host tool error: " + str(e)}],
                "isError": True,
            }
        if isinstance(val, dict) and "__image__" in val:
            return {
                "content": [
                    {
                        "type": "image",
                        "data": val["__image__"],
                        "mimeType": val.get("__mime__", "image/png"),
                    }
                ],
                "isError": False,
            }
        if isinstance(val, dict) and "__error__" in val:
            return {"content": [{"type": "text", "text": val["__error__"]}], "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(val, indent=2)}], "isError": False}
    return {"content": [{"type": "text", "text": "unknown tool: " + name}], "isError": True}


def handle(msg):
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        pv = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        return make_result(
            rid,
            {
                "protocolVersion": pv,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "legacy-host-shim", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return make_result(rid, {})
    if method == "tools/list":
        return make_result(rid, tools_list())
    if method == "tools/call":
        p = msg.get("params") or {}
        return make_result(rid, call_tool(p.get("name"), p.get("arguments") or {}))
    if method and method.startswith("notifications/"):
        return None
    if rid is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {"code": -32601, "message": "method not found: " + str(method)},
    }


def main():
    global VM, AGENT_ADDR
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--vm", required=True, help="libvirt VM name")
    p.add_argument(
        "--agent", default=None, help="in-guest agent ip:port (default: auto-resolve, :9009)"
    )
    args = p.parse_args()
    VM = args.vm
    if args.agent:
        ip, _, port = args.agent.partition(":")
        AGENT_ADDR = (ip, int(port) if port else 9009)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
