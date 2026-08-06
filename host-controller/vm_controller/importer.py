"""evmc-import — the field end of the handoff: ``evmctl import <artwork.zip>``.

A dedicated exhibition host receives ONE baked zip (from the catalog) and this
turns it into a running-ready libvirt VM in one command. The zip is
self-contained — a flattened standalone ``vm/disk.qcow2`` plus the domain
template, the exhibition config, the plugin and (optionally) the conservation
archive. Nothing here needs a golden base or a catalog; that was all resolved at
bake time.

Steps (all under one install root, idempotent-ish via ``--force``):

  1. unpack + ``verify_bundle`` (re-hash every file; refuse a tampered bundle);
  2. place ``disk.qcow2`` at ``<install>/<id>/disk.qcow2``;
  3. rewrite the domain template's ``<name>`` + main-disk ``<source file>`` to the
     local paths and ``virsh define`` it;
  4. create the ``ready`` snapshot the runtime reverts to;
  5. install the plugin into the runtime's plugins dir;
  6. write the per-artwork ``config.yaml`` the runtime loads;
  7. copy the conservation archive into place.

The libvirt/qemu side runs through an injectable ``run`` callable so the flow is
unit-tested without a live host; ``--dry-run`` records the plan without touching
libvirt.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from vmctl_core import promotion

logger = logging.getLogger(__name__)

QEMU_NS = "http://libvirt.org/schemas/domain/qemu/1.0"

Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _real_run(cmd: list[str]) -> subprocess.CompletedProcess:
    logger.info("run: %s", " ".join(cmd))
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _best_effort(run: Runner, cmd: list[str]) -> None:
    """Run a cleanup command, swallowing failures (e.g. the domain/snapshot doesn't exist)."""
    try:
        run(cmd)
    except Exception as e:  # noqa: BLE001 — teardown must not abort the re-import
        logger.info("teardown step skipped (%s): %s", " ".join(cmd), e)


def _teardown_domain(run: Runner, vm_name: str, ready_snapshot: str) -> None:
    """Tear down a previously-imported libvirt domain + its snapshots before a force re-import.

    Removing only the install dir (disk + xml) leaves the OLD libvirt domain still defined and its
    ``ready`` snapshot metadata dangling at paths we are about to delete — so recovery would revert
    to a snapshot backed by a removed file, and ``snapshot-create-as`` below collides with the
    stale snapshot. Destroy (if running), drop the ready snapshot, then undefine with snapshot +
    nvram metadata so the redefine starts from a clean slate. All best-effort: a fresh host where
    the domain was never defined is fine."""
    _best_effort(run, ["virsh", "destroy", vm_name])
    _best_effort(run, ["virsh", "snapshot-delete", vm_name, ready_snapshot, "--children"])
    _best_effort(run, ["virsh", "undefine", vm_name, "--snapshots-metadata", "--nvram"])


class ImportConfig(BaseSettings):
    """Where an imported artwork lands on the exhibition host."""

    model_config = SettingsConfigDict(
        env_prefix="VMCTL_IMPORT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    install_root: str = Field(
        default="/var/lib/evmc/artworks", description="Per-artwork disk + domain xml live here"
    )
    config_dir: str = Field(
        default="/etc/evmc/artworks", description="Per-artwork runtime config.yaml is written here"
    )
    plugins_dir: str = Field(
        default="/var/lib/evmc/plugins", description="The runtime controller's plugins dir"
    )
    archive_dir: str = Field(
        default="/var/lib/evmc/archive", description="Conservation archive store root"
    )


@dataclass
class ImportPlan:
    artwork_id: str
    vm_name: str
    ready_snapshot: str
    install_dir: Path
    disks: list[tuple[str, Path]]  # (source rel in bundle, dest on host), domain order
    domain_path: Path
    config_path: Path
    plugin_dir: Path | None
    archive_dir: Path | None
    host_requires: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def disk_paths(self) -> list[Path]:
        return [dest for _, dest in self.disks]


class BundleImportError(RuntimeError):
    """A bundle that cannot be safely imported (tampered, unsupported domain, exists)."""


def _disk_artifacts(vm: dict) -> list[tuple[str, str]]:
    """Ordered (source-rel-in-bundle, dest-filename) for a baked bundle's disk(s).

    ``baked`` -> one ``vm/disk.qcow2`` -> ``disk.qcow2``. ``baked-multi`` (macOS:
    OpenCore aux + main) -> the ``vm.disks`` list, aux-first, kept in that order so
    the domain's disks map back positionally."""
    profile = vm.get("profile")
    if profile == "baked-multi":
        return [(d["path"], Path(d["path"]).name) for d in (vm.get("disks") or [])]
    if profile == "baked" or vm.get("artifact"):
        rel = (vm.get("artifact") or {}).get("path", "vm/disk.qcow2")
        return [(rel, "disk.qcow2")]
    return []


def _rewrite_domain(xml_text: str, *, vm_name: str, disk_paths: list[Path]) -> str:
    """Repoint a promoted domain at the local baked disk(s) + name; refuse a mismatch.

    Each ``device='disk'`` (in document order) is repointed to ``disk_paths[i]`` — one
    entry for a single-disk guest, aux-first + main for macOS. A domain whose disk
    count doesn't match what the bundle ships, or one that boots a disk through a
    ``<qemu:arg value='file=...qcow2'>`` escape hatch, is rejected rather than
    silently mis-wired. Non-disk ``<qemu:commandline>`` (e.g. macOS isa-applesmc)
    and the ``<loader>`` firmware line are preserved untouched."""
    ET.register_namespace("qemu", QEMU_NS)
    root = ET.fromstring(xml_text)

    # a DISK passed via qemu:arg (not a <disk> element) can't be repointed — refuse.
    for arg in root.iter(f"{{{QEMU_NS}}}arg"):
        val = arg.get("value", "")
        if "file=" in val and ".qcow2" in val:
            raise BundleImportError(
                "domain boots a disk via <qemu:arg file=...qcow2> (disk-over-commandline); "
                "not baked-importable"
            )

    disks = [d for d in root.iter("disk") if d.get("device") == "disk"]
    if len(disks) != len(disk_paths):
        raise BundleImportError(
            f"domain has {len(disks)} disk(s) but the bundle provides {len(disk_paths)}; "
            "refusing to mis-wire"
        )
    for disk, path in zip(disks, disk_paths):
        src = disk.find("source")
        if src is None:
            src = ET.SubElement(disk, "source")
        src.set("file", str(path))
        # a baked disk has no backing chain — drop any inherited backingStore
        for bs in list(disk.findall("backingStore")):
            disk.remove(bs)

    name_el = root.find("name")
    if name_el is None:
        name_el = ET.SubElement(root, "name")
    name_el.text = vm_name

    return ET.tostring(root, encoding="unicode")


def plan_import(bundle_root: Path, cfg: ImportConfig) -> ImportPlan:
    """Compute paths + libvirt commands for an unpacked, verified bundle (no side effects)."""
    by = promotion.read_yaml(bundle_root / promotion.BUNDLE_YAML)
    vm = by.get("vm") or {}
    artifact = vm.get("artifact") or {}
    config_seed = {}
    cfg_file = bundle_root / "config" / "exhibition.config.yaml"
    if cfg_file.is_file():
        config_seed = promotion.read_yaml(cfg_file)

    artwork_id = bundle_root.name
    vm_name = config_seed.get("vm_name") or artwork_id
    ready = (
        config_seed.get("snapshot_name")
        or artifact.get("ready_snapshot")
        or vm.get("ready_snapshot")
        or "ready"
    )

    install_dir = Path(cfg.install_root) / artwork_id
    disks = [(rel, install_dir / name) for rel, name in _disk_artifacts(vm)]
    plan = ImportPlan(
        artwork_id=artwork_id,
        vm_name=vm_name,
        ready_snapshot=ready,
        install_dir=install_dir,
        disks=disks,
        domain_path=install_dir / "domain.xml",
        config_path=Path(cfg.config_dir) / f"{artwork_id}.yaml",
        plugin_dir=None,
        archive_dir=None,
        host_requires=list(vm.get("host_requires") or []),
    )

    plugin = by.get("plugin") or {}
    pid = plugin.get("id")
    if (bundle_root / "plugin").is_dir() and pid:
        plan.plugin_dir = Path(cfg.plugins_dir) / pid

    conservation = by.get("conservation") or {}
    if conservation.get("included") and (bundle_root / "conservation" / "archive").is_dir():
        plan.archive_dir = Path(cfg.archive_dir) / artwork_id

    plan.commands = [
        ["virsh", "define", str(plan.domain_path)],
        # the imported VM is shut off — a plain internal snapshot is the pristine
        # revert target the runtime reverts to; --disk-only/--atomic are for LIVE VMs.
        ["virsh", "snapshot-create-as", vm_name, ready, "--description", "evmc import baseline"],
    ]
    return plan


def import_bundle(
    zip_path: str | Path,
    cfg: ImportConfig | None = None,
    *,
    run: Runner = _real_run,
    dry_run: bool = False,
    force: bool = False,
    staging: str | Path | None = None,
) -> dict:
    """Import a baked artwork zip into libvirt on this host. Returns a summary dict."""
    cfg = cfg or ImportConfig()
    tmp = tempfile.mkdtemp(prefix="evmc-import-", dir=str(staging) if staging else None)
    try:
        root = promotion.unpack_bundle_zip(zip_path, tmp)
        v = promotion.verify_bundle(root)
        if not v["ok"]:
            raise BundleImportError(f"bundle fails verification, refusing to import: {v}")

        vm = (promotion.read_yaml(root / promotion.BUNDLE_YAML).get("vm")) or {}
        if vm and vm.get("profile") not in ("baked", "baked-multi"):
            raise BundleImportError(
                f"expected a baked bundle, got profile={vm.get('profile')!r}; "
                "download it through the catalog (which bakes) rather than copying the drive dir"
            )

        plan = plan_import(root, cfg)

        if plan.install_dir.exists() and not force:
            raise BundleImportError(
                f"{plan.install_dir} already exists — pass force=True to re-import"
            )

        # domain rewrite is pure; do it before any filesystem mutation so a bad
        # domain fails the import cleanly with nothing half-installed.
        domain_xml = None
        host_notes: list[str] = []
        if vm:
            tmpl = root / (vm.get("domain_template") or "vm/domain.template.xml")
            if not tmpl.is_file():
                raise BundleImportError(f"bundle declares a VM but has no domain template: {tmpl}")
            domain_xml = _rewrite_domain(
                tmpl.read_text(), vm_name=plan.vm_name, disk_paths=plan.disk_paths
            )
            # host firmware the baked image can't carry (macOS: OVMF). Warn, don't fail —
            # a curator may install it after import; but surface it loudly.
            for req in plan.host_requires:
                if "/" in req and not Path(req).is_file():
                    host_notes.append(f"host prerequisite missing: {req}")

        summary = {
            "artwork_id": plan.artwork_id,
            "vm_name": plan.vm_name,
            "ready_snapshot": plan.ready_snapshot,
            "install_dir": str(plan.install_dir),
            "has_vm": bool(vm),
            "profile": vm.get("profile") if vm else None,
            "disks": [str(dest) for _, dest in plan.disks],
            "host_requires": plan.host_requires,
            "plugin_dir": str(plan.plugin_dir) if plan.plugin_dir else None,
            "archive_dir": str(plan.archive_dir) if plan.archive_dir else None,
            "commands": plan.commands if vm else [],
            "dry_run": dry_run,
        }
        if host_notes:
            summary.setdefault("notes", []).extend(host_notes)
        if dry_run:
            summary["planned"] = True
            return summary

        # --- materialize -----------------------------------------------------
        if plan.install_dir.exists() and force:
            # Tear down the previously-imported libvirt domain + snapshots BEFORE deleting the
            # disk files, so a re-import doesn't leave a stale domain / 'ready' snapshot pointing
            # at removed paths (see _teardown_domain).
            if vm:
                _teardown_domain(run, plan.vm_name, plan.ready_snapshot)
            shutil.rmtree(plan.install_dir)
        plan.install_dir.mkdir(parents=True, exist_ok=True)

        if vm:
            for rel, dest in plan.disks:
                shutil.copy2(root / rel, dest)
            plan.domain_path.write_text(domain_xml or "")
            run(["virsh", "define", str(plan.domain_path)])
            try:
                # imported VM is shut off -> plain internal snapshot as the revert target
                run(
                    [
                        "virsh",
                        "snapshot-create-as",
                        plan.vm_name,
                        plan.ready_snapshot,
                        "--description",
                        "evmc import baseline",
                    ]
                )
            except subprocess.CalledProcessError as e:
                # FAIL CLOSED: the 'ready' snapshot IS the recovery baseline the runtime reverts
                # to. An import that defined a domain with no baseline would put an exhibit on the
                # floor that can never self-recover — yet the old code swallowed this into a note
                # and still reported imported:True. Refuse the import instead.
                raise BundleImportError(
                    f"ready-snapshot '{plan.ready_snapshot}' could not be created for VM "
                    f"'{plan.vm_name}' ({e}); refusing to import an exhibit with no recovery "
                    f"baseline. Fix the host/libvirt state and re-import."
                ) from e

        if plan.plugin_dir is not None:
            if plan.plugin_dir.exists():
                shutil.rmtree(plan.plugin_dir)
            plan.plugin_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / "plugin", plan.plugin_dir)

        # per-artwork runtime config, with the resolved names pinned
        seed = {}
        cfg_file = root / "config" / "exhibition.config.yaml"
        if cfg_file.is_file():
            seed = promotion.read_yaml(cfg_file)
        seed["vm_name"] = plan.vm_name
        seed["snapshot_name"] = plan.ready_snapshot
        plan.config_path.parent.mkdir(parents=True, exist_ok=True)
        plan.config_path.write_text(yaml.safe_dump(seed, sort_keys=False, allow_unicode=True))
        summary["config_path"] = str(plan.config_path)

        if plan.archive_dir is not None:
            if plan.archive_dir.exists():
                shutil.rmtree(plan.archive_dir)
            plan.archive_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / "conservation" / "archive", plan.archive_dir)

        summary["imported"] = True
        return summary
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evmctl-import", description="Import a baked artwork zip")
    ap.add_argument("zip", help="the artwork .zip downloaded from the catalog")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, touch nothing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing install")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        summary = import_bundle(args.zip, dry_run=args.dry_run, force=args.force)
    except BundleImportError as e:
        print(f"import refused: {e}", file=sys.stderr)
        return 2
    print(yaml.safe_dump(summary, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
