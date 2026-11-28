#!/usr/bin/env python3
"""Upload a local file to a VM via QEMU guest agent."""

import sys
from pathlib import Path

from vmctl_core.backends import LibvirtBackend

from guest_cli import DEFAULT_URI, guest_cli_args


def guest_write_file(
    vm_name: str, guest_path: str, local_path: str, *, uri: str = DEFAULT_URI
) -> None:
    data = Path(local_path).read_bytes()
    LibvirtBackend(vm_name, uri=uri).guest_write_file(guest_path, data)
    print(f"  {local_path} -> {guest_path} ({len(data)} bytes)")


def main(argv: list[str] | None = None) -> int:
    uri, (vm, guest_path, local_path) = guest_cli_args(argv, "VM", "GUEST_PATH", "LOCAL_PATH")
    guest_write_file(vm, guest_path, local_path, uri=uri)
    return 0


if __name__ == "__main__":
    sys.exit(main())
