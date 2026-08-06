#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Legacy in-guest MCP agent — runs on Windows XP (Python 3.4).

A hand-rolled MCP server for legacy guests that Ghost Commander can't reach
(GC needs modern Windows). It speaks the MCP wire protocol directly — JSON-RPC
2.0 over a newline-delimited stream — so it needs NO third-party packages and
NO modern Python: only the 3.4 standard library (json, socketserver, ctypes,
subprocess). That is the newest Python with official Windows XP support.

It exposes Ghost-Commander-style introspection tools (run_shell, processes,
windows, files, screenshot) so a remote AI agent can investigate a black-box
artwork from inside the guest.

Transport
---------
MCP's stdio transport is just newline-delimited JSON over a pipe. Since Claude
Code on the host can't launch a process *inside* the VM, this runs as a TCP
server speaking the exact same framing, and you bridge stdio<->TCP with socat:

    claude mcp add legacy-xp -- socat - TCP:192.168.122.50:9009

No HTTP, no SSE, no session machinery — the hard transport problem disappears.

Run inside the guest (bake into the 'ready' snapshot, autostart):
    python legacy_agent.py                 # TCP server on 0.0.0.0:9009
    python legacy_agent.py --port 9009
    python legacy_agent.py --stdio         # for local testing / piping

NO AUTH — trusted museum LAN only, same posture as Ghost Commander.

Compatibility: written to run on Python 3.4 (Windows XP) AND modern Python, so
the protocol + generic tools can be tested on a Linux dev box. Windows-only
tools degrade gracefully elsewhere.
"""

import argparse
import base64
import json
import os
import platform
import socket
import socketserver
import subprocess
import sys

AGENT_VERSION = "0.1.0"
IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _decode(data):
    """Decode subprocess bytes; XP console is OEM/ANSI, so be forgiving."""
    if not data:
        return ""
    enc = "mbcs" if IS_WINDOWS else "utf-8"
    try:
        return data.decode(enc, "replace")
    except LookupError:
        return data.decode("utf-8", "replace")


def _no_window_flags():
    """CREATE_NO_WINDOW so shelling out doesn't flash a cmd window on the guest."""
    return 0x08000000 if IS_WINDOWS else 0


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def tool_system_info(args):
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "cwd": os.getcwd(),
        "is_windows": IS_WINDOWS,
    }


def tool_run_shell(args):
    command = args["command"]
    timeout = args.get("timeout", 30)
    if IS_WINDOWS:
        argv = ["cmd", "/c", command]
    else:
        argv = ["/bin/sh", "-c", command]

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_no_window_flags(),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return {
            "exit_code": None,
            "timed_out": True,
            "stdout": _decode(out),
            "stderr": _decode(err),
        }
    return {
        "exit_code": proc.returncode,
        "timed_out": False,
        "stdout": _decode(out),
        "stderr": _decode(err),
    }


def tool_list_processes(args):
    if IS_WINDOWS:
        # CSV tasklist is stable to parse across XP -> 10.
        out = subprocess.Popen(
            ["tasklist", "/fo", "csv", "/nh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_no_window_flags(),
        ).communicate()[0]
        procs = []
        import csv
        import io

        for row in csv.reader(io.StringIO(_decode(out))):
            if len(row) >= 5:
                procs.append({"name": row[0], "pid": row[1], "mem": row[4]})
        return {"count": len(procs), "processes": procs}
    else:
        out = subprocess.Popen(
            ["ps", "-eo", "pid,comm,rss"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).communicate()[0]
        lines = _decode(out).splitlines()[1:]
        procs = []
        for ln in lines:
            parts = ln.split(None, 2)
            if len(parts) >= 2:
                procs.append(
                    {"pid": parts[0], "name": parts[1], "mem": parts[2] if len(parts) > 2 else ""}
                )
        return {"count": len(procs), "processes": procs}


def tool_list_windows(args):
    if not IS_WINDOWS:
        return {"windows": [], "note": "window enumeration is Windows-only"}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    windows = []

    def _cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                windows.append({"hwnd": int(hwnd), "title": buf.value, "pid": int(pid.value)})
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return {"count": len(windows), "windows": windows}


def tool_list_dir(args):
    path = args["path"]
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            is_dir = os.path.isdir(full)
            size = os.path.getsize(full) if not is_dir else None
        except OSError:
            is_dir, size = False, None
        entries.append({"name": name, "is_dir": is_dir, "size": size})
    return {"path": path, "count": len(entries), "entries": entries}


def tool_read_file(args):
    path = args["path"]
    max_bytes = args.get("max_bytes", 1024 * 1024)
    binary = args.get("binary", False)
    f = open(path, "rb")
    try:
        data = f.read(max_bytes)
    finally:
        f.close()
    if binary:
        return {"path": path, "bytes": len(data), "base64": base64.b64encode(data).decode("ascii")}
    return {"path": path, "bytes": len(data), "text": data.decode("utf-8", "replace")}


def tool_write_file(args):
    path = args["path"]
    if args.get("base64"):
        data = base64.b64decode(args["content"])
    else:
        data = args["content"].encode("utf-8")
    f = open(path, "wb")
    try:
        f.write(data)
    finally:
        f.close()
    return {"path": path, "bytes": len(data)}


def tool_env(args):
    """Return the guest's environment variables (e.g. APPDATA for profile discovery)."""
    return dict(os.environ)


def tool_find_files(args):
    """Recursively find files under a directory matching a glob pattern.

    Returns {dir, pattern, count, files} — the same shape the modern (Go) agent returns, so the
    conservation callers (Java cacerts / Mozilla profile discovery) work against either agent."""
    import fnmatch

    root = args["dir"]
    pattern = args.get("pattern", "*")
    limit = int(args.get("limit", 200))
    matches = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fnmatch.fnmatch(fn, pattern):
                matches.append(os.path.join(dirpath, fn))
                if len(matches) >= limit:
                    return {
                        "dir": root,
                        "pattern": pattern,
                        "count": len(matches),
                        "files": matches,
                    }
    return {"dir": root, "pattern": pattern, "count": len(matches), "files": matches}


# --- registry (winreg; Windows-only, degrades gracefully elsewhere) ---------
_HIVES = {
    "HKCU": "HKEY_CURRENT_USER",
    "HKEY_CURRENT_USER": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKEY_LOCAL_MACHINE": "HKEY_LOCAL_MACHINE",
    "HKCR": "HKEY_CLASSES_ROOT",
    "HKEY_CLASSES_ROOT": "HKEY_CLASSES_ROOT",
    "HKU": "HKEY_USERS",
    "HKEY_USERS": "HKEY_USERS",
}


def _winreg():
    """Import winreg lazily; returns the module or None off-Windows / if unavailable."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg

        return winreg
    except ImportError:
        return None


def _hive(wr, name):
    return getattr(wr, _HIVES.get((name or "HKCU").upper(), "HKEY_CURRENT_USER"))


def tool_reg_set(args):
    wr = _winreg()
    if wr is None:
        return {"__error__": "reg_set is Windows-only"}
    typ = args.get("type", "sz")
    rtmap = {"sz": wr.REG_SZ, "expand_sz": wr.REG_EXPAND_SZ, "dword": wr.REG_DWORD}
    if typ not in rtmap:
        return {"__error__": "unsupported type %r (sz|dword|expand_sz)" % typ}
    data = int(args.get("data", 0)) if typ == "dword" else str(args.get("data", ""))
    key = args.get("key", "")
    value = args.get("value", "")
    h = wr.CreateKeyEx(_hive(wr, args.get("hive")), key, 0, wr.KEY_WRITE)
    try:
        wr.SetValueEx(h, value, 0, rtmap[typ], data)
    finally:
        wr.CloseKey(h)
    return {"ok": True, "key": key, "value": value, "type": typ}


def tool_reg_get(args):
    wr = _winreg()
    if wr is None:
        return {"__error__": "reg_get is Windows-only"}
    try:
        h = wr.OpenKey(_hive(wr, args.get("hive")), args.get("key", ""), 0, wr.KEY_READ)
    except OSError:
        return {"exists": False}
    try:
        data, typ = wr.QueryValueEx(h, args.get("value", ""))
    except OSError:
        return {"exists": False}
    finally:
        wr.CloseKey(h)
    if isinstance(data, (bytes, bytearray)):
        data = base64.b64encode(bytes(data)).decode("ascii")
    return {"exists": True, "type": typ, "data": data}


def tool_reg_delete(args):
    wr = _winreg()
    if wr is None:
        return {"__error__": "reg_delete is Windows-only"}
    hive = _hive(wr, args.get("hive"))
    key = args.get("key", "")
    if "value" in args:
        try:
            h = wr.OpenKey(hive, key, 0, wr.KEY_SET_VALUE)
        except OSError:
            return {"ok": True, "note": "key absent"}
        try:
            wr.DeleteValue(h, args.get("value", ""))
        finally:
            wr.CloseKey(h)
        return {"ok": True, "deleted": "value"}
    try:
        wr.DeleteKey(hive, key)
    except OSError as exc:
        return {"__error__": "delete key failed: %r" % exc}
    return {"ok": True, "deleted": "key"}


def tool_show_window(args):
    """Change a window's state: hide|show|minimize|maximize|restore|foreground|close."""
    if not IS_WINDOWS:
        return {"__error__": "show_window is Windows-only"}
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = int(args.get("hwnd", 0))
    mode = args.get("mode", "")
    modes = {"hide": 0, "show": 5, "minimize": 6, "maximize": 3, "restore": 9}
    if mode == "foreground":
        user32.SetForegroundWindow(hwnd)
    elif mode == "close":
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    elif mode in modes:
        user32.ShowWindow(hwnd, modes[mode])
    else:
        return {"__error__": "unknown mode %r" % mode}
    return {"ok": True}


def _cert_der(args):
    """Decode a cert to DER from either 'pem' text or 'der_base64'. Returns bytes or None."""
    pem = args.get("pem")
    if pem:
        b64 = "".join(
            ln.strip()
            for ln in pem.splitlines()
            if ln.strip() and not ln.strip().startswith("-----")
        )
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    der_b64 = args.get("der_base64")
    if der_b64:
        try:
            return base64.b64decode(der_b64)
        except Exception:
            return None
    return None


def tool_install_cert(args):
    """Install a CA into the Windows trust store SILENTLY by writing the cert store's registry blob.

    The certificate store on Windows *is* the registry (Software\\Microsoft\\SystemCertificates\\
    <store>\\Certificates\\<sha1-thumbprint>); writing the serialized cert element (property id 0x20
    = encoded cert) directly avoids crypt32's modal "Security Warning" trust prompt that would block
    unattended use on a kiosk. Mirrors the modern Go agent. Give 'pem' or 'der_base64'."""
    if not IS_WINDOWS:
        return {"__error__": "install_cert is Windows-only; use host-side trust install elsewhere"}
    wr = _winreg()
    if wr is None:
        return {"__error__": "winreg unavailable"}
    import hashlib
    import struct

    der = _cert_der(args)
    if der is None:
        return {"__error__": "provide a valid 'pem' or 'der_base64'"}
    thumb = hashlib.sha1(der).hexdigest().upper()
    store = args.get("store", "ROOT")
    scope = args.get("scope", "user")
    hive = wr.HKEY_LOCAL_MACHINE if scope == "machine" else wr.HKEY_CURRENT_USER
    key_path = "Software\\Microsoft\\SystemCertificates\\" + store + "\\Certificates\\" + thumb
    # one CERT property element: <id=0x20 encoded-cert><flags=1><len><DER>
    blob = struct.pack("<III", 0x20, 1, len(der)) + der
    try:
        h = wr.CreateKeyEx(hive, key_path, 0, wr.KEY_WRITE)
    except OSError as exc:
        return {"__error__": "RegCreateKeyEx failed (admin needed for machine scope?): %r" % exc}
    try:
        wr.SetValueEx(h, "Blob", 0, wr.REG_BINARY, blob)
    finally:
        wr.CloseKey(h)
    return {
        "installed": True,
        "silent": True,
        "store": store,
        "scope": scope,
        "thumbprint": thumb,
        "bytes": len(der),
    }


def tool_screenshot(args):
    """Capture the desktop via GDI (no PIL needed). Returns a base64 BMP.

    For high-quality PNG with zero in-guest code, prefer host-side
    `virsh screenshot` — it grabs the framebuffer at the hypervisor level and
    works even when this agent (or the whole guest OS) can't.
    """
    if not IS_WINDOWS:
        return {"__error__": "screenshot is Windows-only here; use host-side virsh screenshot"}
    import ctypes
    import struct
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    SM_XV, SM_YV, SM_CXV, SM_CYV = 76, 77, 78, 79
    x = user32.GetSystemMetrics(SM_XV)
    y = user32.GetSystemMetrics(SM_YV)
    w = user32.GetSystemMetrics(SM_CXV)
    h = user32.GetSystemMetrics(SM_CYV)

    desktop = user32.GetDesktopWindow()
    src_dc = user32.GetWindowDC(desktop)
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, w, h, src_dc, x, y, 0x00CC0020)  # SRCCOPY

    class BMIH(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth, bi.biHeight = w, h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 24, 0
    row = (w * 3 + 3) & ~3
    img_size = row * h
    buf = ctypes.create_string_buffer(img_size)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), 0)  # DIB_RGB_COLORS

    file_hdr = struct.pack("<2sIHHI", b"BM", 14 + 40 + img_size, 0, 0, 14 + 40)
    info_hdr = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, img_size, 0, 0, 0, 0)
    bmp_bytes = file_hdr + info_hdr + buf.raw

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(desktop, src_dc)
    return {
        "__image__": base64.b64encode(bmp_bytes).decode("ascii"),
        "__mime__": "image/bmp",
        "width": w,
        "height": h,
    }


# name -> (handler, description, inputSchema)
def _obj(props, required):
    return {"type": "object", "properties": props, "required": required}


TOOLS = {
    "system_info": (
        tool_system_info,
        "OS, hostname, Python version and cwd of the guest.",
        _obj({}, []),
    ),
    "run_shell": (
        tool_run_shell,
        "Run a shell/cmd command in the guest; returns stdout, stderr, exit code.",
        _obj(
            {
                "command": {"type": "string"},
                "timeout": {"type": "number", "description": "seconds (default 30)"},
            },
            ["command"],
        ),
    ),
    "list_processes": (
        tool_list_processes,
        "List running processes (name, pid, memory).",
        _obj({}, []),
    ),
    "list_windows": (
        tool_list_windows,
        "List visible top-level windows (title, hwnd, pid). Windows only.",
        _obj({}, []),
    ),
    "list_dir": (
        tool_list_dir,
        "List a directory's entries (name, is_dir, size).",
        _obj({"path": {"type": "string"}}, ["path"]),
    ),
    "read_file": (
        tool_read_file,
        "Read a file from the guest (text by default; set binary=true for base64).",
        _obj(
            {
                "path": {"type": "string"},
                "max_bytes": {"type": "number"},
                "binary": {"type": "boolean"},
            },
            ["path"],
        ),
    ),
    "write_file": (
        tool_write_file,
        "Write a file in the guest (utf-8 text, or base64 if base64=true).",
        _obj(
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "base64": {"type": "boolean"},
            },
            ["path", "content"],
        ),
    ),
    "screenshot": (
        tool_screenshot,
        "Capture the desktop as a base64 BMP (GDI; Windows only).",
        _obj({}, []),
    ),
    "env": (
        tool_env,
        "Return the guest's environment variables.",
        _obj({}, []),
    ),
    "find_files": (
        tool_find_files,
        "Recursively find files under a directory matching a glob pattern.",
        _obj(
            {
                "dir": {"type": "string"},
                "pattern": {"type": "string", "description": "glob, e.g. *.dir"},
                "limit": {"type": "number"},
            },
            ["dir"],
        ),
    ),
    "reg_get": (
        tool_reg_get,
        "Read a registry value. Returns {exists, type, data}. Windows only.",
        _obj(
            {"hive": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}},
            ["hive", "key"],
        ),
    ),
    "reg_set": (
        tool_reg_set,
        "Set a registry value (type sz|dword|expand_sz). Windows only.",
        _obj(
            {
                "hive": {"type": "string", "description": "HKCU|HKLM|HKCR|HKU"},
                "key": {"type": "string"},
                "value": {"type": "string", "description": "value name ('' = default)"},
                "type": {"type": "string", "description": "sz|dword|expand_sz"},
                "data": {"description": "string or number"},
            },
            ["hive", "key"],
        ),
    ),
    "reg_delete": (
        tool_reg_delete,
        "Delete a registry value (if 'value' given) or key. Windows only.",
        _obj(
            {"hive": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"}},
            ["hive", "key"],
        ),
    ),
    "show_window": (
        tool_show_window,
        "Change a window state: hide|show|minimize|maximize|restore|foreground|close (Windows).",
        _obj({"hwnd": {"type": "number"}, "mode": {"type": "string"}}, ["hwnd", "mode"]),
    ),
    "install_cert": (
        tool_install_cert,
        "Install a CA into the Windows trust store silently (registry blob). Give 'pem' or "
        "'der_base64'. Windows only.",
        _obj(
            {
                "pem": {"type": "string", "description": "full PEM cert text"},
                "der_base64": {"type": "string", "description": "base64 of DER cert (alt to pem)"},
                "scope": {"type": "string", "description": "user (default) or machine"},
                "store": {"type": "string", "description": "store name, default ROOT"},
            },
            [],
        ),
    ),
}


# ---------------------------------------------------------------------------
# MCP protocol (JSON-RPC 2.0)
# ---------------------------------------------------------------------------


def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tools_list():
    out = []
    for name in sorted(TOOLS):
        _handler, desc, schema = TOOLS[name]
        out.append({"name": name, "description": desc, "inputSchema": schema})
    return {"tools": out}


def _call_tool(name, arguments):
    if name not in TOOLS:
        return {"content": [{"type": "text", "text": "unknown tool: " + name}], "isError": True}
    handler = TOOLS[name][0]
    try:
        value = handler(arguments or {})
    except Exception as exc:
        return {"content": [{"type": "text", "text": "tool error: " + repr(exc)}], "isError": True}

    if isinstance(value, dict) and "__image__" in value:
        return {
            "content": [
                {
                    "type": "image",
                    "data": value["__image__"],
                    "mimeType": value.get("__mime__", "image/bmp"),
                }
            ],
            "isError": False,
        }
    if isinstance(value, dict) and "__error__" in value:
        return {"content": [{"type": "text", "text": value["__error__"]}], "isError": True}
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle_message(msg):
    """Return a response dict, or None for notifications."""
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        return _result(
            mid,
            {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "legacy-guest-agent", "version": AGENT_VERSION},
            },
        )
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, _tools_list())
    if method == "tools/call":
        params = msg.get("params") or {}
        return _result(mid, _call_tool(params.get("name"), params.get("arguments")))
    if method and method.startswith("notifications/"):
        return None  # notifications get no response
    if mid is None:
        return None
    return _error(mid, -32601, "method not found: " + str(method))


def process_line(line):
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except ValueError:
        return _error(None, -32700, "parse error")
    return handle_message(msg)


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------


class MCPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            raw = self.rfile.readline()
            if not raw:
                break
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", "replace")
            response = process_line(text)
            if response is not None:
                self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
                self.wfile.flush()


class ThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_stdio():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        response = process_line(line)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


def main():
    p = argparse.ArgumentParser(description="Legacy in-guest MCP agent (Python 3.4 / Windows XP)")
    p.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    p.add_argument("--port", type=int, default=9009, help="TCP port (default 9009)")
    p.add_argument("--stdio", action="store_true", help="run over stdin/stdout instead of TCP")
    args = p.parse_args()

    if args.stdio:
        serve_stdio()
        return

    server = ThreadingTCPServer((args.host, args.port), MCPHandler)
    sys.stderr.write(
        "legacy-guest-agent {0} listening on {1}:{2}\n".format(AGENT_VERSION, args.host, args.port)
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
