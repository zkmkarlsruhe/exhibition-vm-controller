#!/usr/bin/env python3
"""Size gate for source files, Python functions and Python classes.

Limits and rationale: https://docs.vmctl.org/contributing/engineering/
Every vmctl repo carries this file as scripts/check_sizes.py; keep the copies identical.

Config: .size-limits.json in the repo root
  {"exclude": ["glob", ...], "baseline": {"path": n, "path::Qual.name": n}}
Baseline entries are existing violations. They may shrink, never grow (ratchet).
New violations fail. `--update` rewrites the baseline to the current state; use it only
to drop entries that got fixed, and say so in the MR.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

FILE_MAX = 800
FUNC_MAX = 100
CLASS_MAX = 500
EXTS = {".py", ".ts", ".tsx", ".js", ".mjs", ".svelte", ".vue", ".sh"}
CONFIG = Path(".size-limits.json")
# generated or third-party code is not ours to split
DEFAULT_EXCLUDE = ["*.min.js", "*.d.ts", "vendor/*", "*/vendor/*", "dist/*", "*/dist/*"]


def tracked_files(exclude: list[str]) -> list[Path]:
    cmd = ["git", "ls-files"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [
        Path(p)
        for p in out.splitlines()
        if Path(p).suffix in EXTS
        and Path(p).is_file()
        and not any(fnmatch.fnmatch(p, g) for g in exclude)
    ]


def python_items(path: Path, source: str) -> dict[str, tuple[int, int]]:
    """qualname -> (lines, limit) for functions and classes over their limit."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}  # e.g. guest agents for old runtimes; the file limit still applies
    found: dict[str, tuple[int, int]] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                lines = (child.end_lineno or child.lineno) - child.lineno + 1
                limit = CLASS_MAX if isinstance(child, ast.ClassDef) else FUNC_MAX
                if lines > limit:
                    found[f"{path}::{name}"] = (lines, limit)
                walk(child, f"{name}.")

    walk(tree, "")
    return found


def measure(exclude: list[str]) -> dict[str, tuple[int, int]]:
    items: dict[str, tuple[int, int]] = {}
    for path in tracked_files(exclude):
        source = path.read_text(encoding="utf-8", errors="replace")
        lines = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
        if lines > FILE_MAX:
            items[str(path)] = (lines, FILE_MAX)
        if path.suffix == ".py":
            items.update(python_items(path, source))
    return items


def main() -> int:
    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    exclude = config.get("exclude", [])
    patterns = DEFAULT_EXCLUDE + exclude
    baseline: dict[str, int] = config.get("baseline", {})
    current = measure(patterns)

    if "--update" in sys.argv:
        config["exclude"] = exclude
        config["baseline"] = {k: v[0] for k, v in sorted(current.items())}
        CONFIG.write_text(json.dumps(config, indent=2) + "\n")
        print(f"{CONFIG}: {len(current)} baseline entries")
        return 0

    errors = []
    for key, (lines, limit) in sorted(current.items()):
        allowed = baseline.get(key)
        if allowed is None:
            errors.append(f"{key}: {lines} lines > limit {limit}. Split it.")
        elif lines > allowed:
            errors.append(f"{key}: {lines} lines > baseline {allowed}. Shrink it.")
    fixed = [k for k in baseline if k not in current]
    for key in fixed:
        print(f"note: {key} is now within limits; drop it from {CONFIG} (--update)")
    for e in errors:
        print(f"error: {e}")
    summary = f"{len(current)} over, {len(baseline)} baselined, {len(errors)} errors"
    print(f"size gate: {summary}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
