#!/usr/bin/env python3
"""Run a command inside a VM via QEMU guest agent and print output."""

import json
import base64
import subprocess
import sys
import time


def guest_exec(vm_name: str, cmd: str, timeout: int = 10) -> tuple[int, str, str]:
    """Execute cmd.exe /c <cmd> inside VM, return (exitcode, stdout, stderr)."""
    result = subprocess.run(
        [
            "virsh",
            "qemu-agent-command",
            vm_name,
            json.dumps(
                {
                    "execute": "guest-exec",
                    "arguments": {
                        "path": "cmd.exe",
                        "arg": ["/c", cmd],
                        "capture-output": True,
                    },
                }
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pid = json.loads(result.stdout)["return"]["pid"]

    for _ in range(timeout * 2):
        time.sleep(0.5)
        result = subprocess.run(
            [
                "virsh",
                "qemu-agent-command",
                vm_name,
                json.dumps(
                    {
                        "execute": "guest-exec-status",
                        "arguments": {"pid": pid},
                    }
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        r = json.loads(result.stdout)["return"]
        if r.get("exited"):
            stdout = base64.b64decode(r.get("out-data", "")).decode("utf-8", errors="replace")
            stderr = base64.b64decode(r.get("err-data", "")).decode("utf-8", errors="replace")
            return r.get("exitcode", -1), stdout, stderr

    return -1, "", "timeout"


def guest_read_file(vm_name: str, path: str) -> bytes:
    """Read a file from the guest via guest-file-open/read/close."""
    # Open
    result = subprocess.run(
        [
            "virsh",
            "qemu-agent-command",
            vm_name,
            json.dumps(
                {
                    "execute": "guest-file-open",
                    "arguments": {"path": path, "mode": "r"},
                }
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    handle = json.loads(result.stdout)["return"]

    # Read in chunks. Wrap in try/finally so a mid-stream read that raises (check=True) still
    # releases the guest file handle — otherwise the handle leaks in the guest agent.
    data = b""
    try:
        while True:
            result = subprocess.run(
                [
                    "virsh",
                    "qemu-agent-command",
                    vm_name,
                    json.dumps(
                        {
                            "execute": "guest-file-read",
                            "arguments": {"handle": handle, "count": 65536},
                        }
                    ),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            r = json.loads(result.stdout)["return"]
            chunk = base64.b64decode(r.get("buf-b64", ""))
            data += chunk
            if r.get("eof", False) or not chunk:
                break
    finally:
        # Close (best-effort; always runs, even if a read above raised)
        subprocess.run(
            [
                "virsh",
                "qemu-agent-command",
                vm_name,
                json.dumps(
                    {
                        "execute": "guest-file-close",
                        "arguments": {"handle": handle},
                    }
                ),
            ],
            capture_output=True,
            text=True,
        )
    return data


if __name__ == "__main__":
    vm = sys.argv[1]
    cmd = sys.argv[2]
    exitcode, stdout, stderr = guest_exec(vm, cmd)
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    sys.exit(exitcode)
