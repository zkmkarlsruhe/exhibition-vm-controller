"""Packaging metadata regression check.

Poetry derives the package directory from the distribution name unless
`packages` is declared. The distribution is `exhibition-vm-controller` but the
source package is `vm_controller`, so without the explicit mapping every build
(`pip install -e .`, wheel) fails with ModuleOrPackageNotFoundError.
"""

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_source_package_is_declared():
    cfg = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    includes = {p["include"] for p in cfg["tool"]["poetry"]["packages"]}
    assert "vm_controller" in includes


def test_console_scripts_target_the_declared_package():
    cfg = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    scripts = cfg["tool"]["poetry"]["scripts"]
    assert scripts
    for name, target in scripts.items():
        assert target.startswith("vm_controller."), f"{name} -> {target}"
