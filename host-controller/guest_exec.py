#!/usr/bin/env python3
"""Run a command inside a VM via QEMU guest agent and print output."""

import sys

from vmctl_core.backends import LibvirtBackend
from vmctl_core.backends.libvirt_backend import MAX_GUEST_READ_BYTES as MAX_GUEST_READ_BYTES

from guest_cli import DEFAULT_URI, guest_cli_args


def guest_exec(
    vm_name: str, cmd: str, timeout: float | None = None, *, uri: str = DEFAULT_URI
) -> tuple[int, str, str]:
    """Execute cmd.exe /c <cmd> inside VM, return (exitcode, stdout, stderr)."""
    backend = LibvirtBackend(vm_name, uri=uri)
    args = ["/c", cmd]
    if timeout is None:
        return backend.guest_exec("cmd.exe", args)
    return backend.guest_exec("cmd.exe", args, timeout=timeout)


def guest_read_file(vm_name: str, path: str, *, uri: str = DEFAULT_URI) -> bytes:
    return LibvirtBackend(vm_name, uri=uri).guest_read_file(path)


def main(argv: list[str] | None = None) -> int:
    uri, (vm, cmd) = guest_cli_args(argv, "VM", "COMMAND")
    exitcode, stdout, stderr = guest_exec(vm, cmd, uri=uri)
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return exitcode


if __name__ == "__main__":
    sys.exit(main())
