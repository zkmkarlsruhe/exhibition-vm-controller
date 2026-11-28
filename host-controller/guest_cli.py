"""Common arguments for the host-side guest tools."""

import argparse
import sys

DEFAULT_URI = "qemu:///system"


def guest_cli_args(argv: list[str] | None, *positionals: str) -> tuple[str, list[str]]:
    parser = argparse.ArgumentParser(
        usage=f"%(prog)s [--uri URI] {' '.join(positionals)}", allow_abbrev=False, add_help=False
    )
    parser.add_argument("--uri", default=DEFAULT_URI)
    # The original scripts accept literal dash-prefixed operands and ignore trailing arguments.
    options, operands = parser.parse_known_args(argv)
    if len(operands) < len(positionals):
        parser.print_usage(sys.stderr)
        parser.exit(1)
    return options.uri, operands[: len(positionals)]
