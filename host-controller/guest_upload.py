#!/usr/bin/env python3
"""Upload a local file to a VM via QEMU guest agent."""

import json
import base64
import subprocess
import sys


def guest_write_file(vm_name: str, guest_path: str, local_path: str) -> None:
    with open(local_path, "rb") as f:
        data = f.read()

    # Open file for writing
    r = subprocess.run(
        [
            "virsh",
            "qemu-agent-command",
            vm_name,
            json.dumps(
                {
                    "execute": "guest-file-open",
                    "arguments": {"path": guest_path, "mode": "w"},
                }
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    handle = json.loads(r.stdout)["return"]

    # Write in chunks (max 48KB base64 ≈ 36KB raw to stay under virsh limits). Wrap in try/finally
    # so a mid-stream write that raises (check=True) still releases the guest file handle —
    # otherwise the handle leaks in the guest agent.
    chunk_size = 36000
    offset = 0
    try:
        while offset < len(data):
            chunk = data[offset : offset + chunk_size]
            subprocess.run(
                [
                    "virsh",
                    "qemu-agent-command",
                    vm_name,
                    json.dumps(
                        {
                            "execute": "guest-file-write",
                            "arguments": {
                                "handle": handle,
                                "buf-b64": base64.b64encode(chunk).decode(),
                            },
                        }
                    ),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            offset += chunk_size
    finally:
        # Close (best-effort; always runs, even if a write above raised)
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
    print(f"  {local_path} -> {guest_path} ({len(data)} bytes)")


if __name__ == "__main__":
    vm = sys.argv[1]
    guest_path = sys.argv[2]
    local_path = sys.argv[3]
    guest_write_file(vm, guest_path, local_path)
