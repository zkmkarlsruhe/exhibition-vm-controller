"""evmctl-import — the field end of the handoff: ``evmctl-import <artwork.zip>``.

A dedicated exhibition host receives ONE baked zip (from the catalog) and this
turns it into a running-ready libvirt VM in one command. The zip is
self-contained — a flattened standalone ``vm/disk.qcow2`` plus the domain
template, the exhibition config, the plugin and (optionally) the conservation
archive. Nothing here needs a golden base or a catalog; that was all resolved at
bake time.

Process bundles carry a complete emulator instance in ``vm/`` instead. Import
copies it into place and creates a cold ``ready`` snapshot through ProcessBackend.

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
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from vmctl_core import promotion
from vmctl_core.backends import BackendError, LibvirtBackend
from vmctl_core.backends.process import ProcessBackend, process_path
from vmctl_core.paths import safe_segment

from vm_controller.vm_names import validate_vm_identifier

logger = logging.getLogger(__name__)

QEMU_NS = "http://libvirt.org/schemas/domain/qemu/1.0"

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@contextmanager
def _best_effort() -> Iterator[None]:
    try:
        yield
    except Exception as e:
        logger.info("teardown step skipped: %s", e)


def _import_domain(name: str, uri: str, run: Runner | None) -> LibvirtBackend:
    if run is None:
        return LibvirtBackend(name, uri)
    return LibvirtBackend(name, uri, runner=lambda argv, _timeout, _check: run(argv))


def _teardown(domain: LibvirtBackend, ready: str) -> None:
    # Remove the old baseline and NVRAM before replacing the disks they refer to.
    with _best_effort():
        domain.stop()
    with _best_effort():
        domain.snapshot_delete(ready, children=True, missing_ok=True)
    with _best_effort():
        domain.undefine(snapshots_metadata=True, nvram=True)


def _define_with_baseline(domain: LibvirtBackend, path: Path, ready: str) -> None:
    domain.define(str(path))
    try:
        domain.snapshot_create(
            ready, description="evmc import baseline", replace=False, timeout=120
        )
    except BackendError as e:
        # A domain without its ready snapshot cannot self-recover.
        raise BundleImportError(
            f"ready-snapshot '{ready}' could not be created for VM "
            f"'{domain.name}' ({e}); refusing to import an exhibit with no recovery "
            f"baseline. Fix the host/libvirt state and re-import."
        ) from e


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
    libvirt_uri: str = Field(
        default="qemu:///system",
        description=(
            "The libvirt the domain is defined in. MUST be the one the runtime controller "
            "connects to (VMManager's default is the same string)"
        ),
    )
    vm_startup_heartbeat_delay: float = Field(
        default=120,
        gt=0,
        allow_inf_nan=False,
        description="Process emulator startup grace in seconds",
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
    for disk, path in zip(disks, disk_paths, strict=False):
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

    try:
        validate_vm_identifier(vm_name, field="vm_name")
        validate_vm_identifier(ready, field="snapshot_name")
    except ValueError as e:
        raise BundleImportError(str(e)) from e

    install_dir = Path(cfg.install_root) / artwork_id
    is_process = vm.get("backend") == "process"
    disks = [] if is_process else [(rel, install_dir / name) for rel, name in _disk_artifacts(vm)]
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
        # plugin.id comes out of the bundle, and the install step rmtree's this directory
        # before copying into it. Joining an unchecked value would let `id: "/etc"` or
        # `id: "../../../../etc/cron.d"` aim that rmtree + copytree anywhere the importer can
        # write — and it runs as root on the exhibition host. verify_bundle cannot help: the
        # submitter writes CHECKSUMS too, so a tampered bundle verifies fine. Every sibling
        # value that reaches a path is already guarded this way (artwork_id via
        # unpack_bundle_zip, the catalog's URL components, core's tool segments); this one
        # was the gap.
        try:
            safe_segment(pid, field="bundle plugin.id")
        except ValueError as e:
            raise BundleImportError(str(e)) from e
        plan.plugin_dir = Path(cfg.plugins_dir) / pid

    conservation = by.get("conservation") or {}
    if conservation.get("included") and (bundle_root / "conservation" / "archive").is_dir():
        plan.archive_dir = Path(cfg.archive_dir) / artwork_id

    if vm and not is_process:
        xml = _domain_template(bundle_root, vm)

        def record(argv: list[str]) -> subprocess.CompletedProcess[str]:
            plan.commands.append(argv)
            return subprocess.CompletedProcess(argv, 0, xml if argv[3] == "dumpxml" else "", "")

        # The backend picks architecture-specific snapshot flags from the domain XML.
        domain = _import_domain(vm_name, cfg.libvirt_uri, record)
        _define_with_baseline(domain, plan.domain_path, ready)
    return plan


def _domain_template(root: Path, vm: dict) -> str:
    try:
        tmpl = promotion.bundle_path(root, vm.get("domain_template") or "vm/domain.template.xml")
    except ValueError as e:
        raise BundleImportError(str(e)) from e
    if not tmpl.is_file():
        raise BundleImportError(f"bundle declares a VM but has no domain template: {tmpl}")
    return tmpl.read_text()


def _libvirt_files(root: Path, vm: dict, plan: ImportPlan) -> tuple[list[tuple[Path, Path]], str]:
    try:
        disks = [(promotion.bundle_path(root, rel), dest) for rel, dest in plan.disks]
    except ValueError as e:
        raise BundleImportError(str(e)) from e
    return disks, _rewrite_domain(
        _domain_template(root, vm), vm_name=plan.vm_name, disk_paths=plan.disk_paths
    )


def import_bundle(
    zip_path: str | Path,
    cfg: ImportConfig | None = None,
    *,
    run: Runner | None = None,
    dry_run: bool = False,
    force: bool = False,
    staging: str | Path | None = None,
) -> dict:
    """Import a baked VM or process artwork zip on this host. Returns a summary dict."""
    cfg = cfg or ImportConfig()
    tmp = tempfile.mkdtemp(prefix="evmc-import-", dir=str(staging) if staging else None)
    # Bound before the try: the failure path and the cleanup both look at them, and a bundle
    # that fails verification returns long before the materialize block runs.
    staged: Path | None = None
    retired: Path | None = None
    try:
        try:
            root = promotion.unpack_bundle_zip(zip_path, tmp)
        except ValueError as e:
            raise BundleImportError(str(e)) from e
        v = promotion.verify_bundle(root)
        if not v["ok"]:
            raise BundleImportError(f"bundle fails verification, refusing to import: {v}")

        vm = (promotion.read_yaml(root / promotion.BUNDLE_YAML).get("vm")) or {}
        is_process = vm.get("backend") == "process"
        if vm and not is_process and vm.get("profile") not in ("baked", "baked-multi"):
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
        domain = None
        domain_xml = None
        host_notes: list[str] = []
        if is_process:
            backend = ProcessBackend(plan.vm_name, instance_dir=root / "vm")
            for name in [*backend.spec.images, *backend.spec.media]:
                if not process_path(root / "vm", name).is_file():
                    raise BundleImportError(f"process payload missing: {name}")
        elif vm:
            domain = _import_domain(plan.vm_name, cfg.libvirt_uri, run)
            disks, domain_xml = _libvirt_files(root, vm, plan)
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
            # summary is a heterogeneous dict (str/bool/list values), so setdefault's type is the
            # union of all of them and .extend is not on it. Build the list explicitly.
            notes = summary.get("notes")
            summary["notes"] = ([*notes] if isinstance(notes, list) else []) + list(host_notes)
        if dry_run:
            summary["planned"] = True
            return summary

        # --- materialize -----------------------------------------------------
        # Order matters on a gallery floor. The old sequence tore the exhibit down FIRST —
        # destroy, snapshot-delete, undefine, rmtree — and only then copied the replacement in,
        # so the most likely failure (no disk space for the new image) landed after the running
        # exhibit's disk was already gone. Stage the new disks BESIDE the old install first;
        # only once they are on disk do we take the old one apart, and a failure after that
        # point puts the previous install back.
        if vm:
            staged = plan.install_dir.with_name(plan.install_dir.name + ".incoming")
            shutil.rmtree(staged, ignore_errors=True)
            if is_process:
                shutil.copytree(root / "vm", staged)
                ProcessBackend(plan.vm_name, instance_dir=staged).snapshot_create(
                    plan.ready_snapshot
                )
            else:
                staged.mkdir(parents=True, exist_ok=True)
                for src, dest in disks:
                    shutil.copy2(src, staged / dest.name)

        if plan.install_dir.exists() and force:
            # The libvirt domain + its ready snapshot must go before the redefine (see
            # _teardown). Note this is NOT reversible: snapshot-delete removes the
            # internal snapshot from the qcow2 itself, so the old recovery baseline is gone
            # either way — which is why the disks are staged before we get here.
            if is_process:
                ProcessBackend(plan.vm_name, instance_dir=plan.install_dir).stop()
            elif domain:
                _teardown(domain, plan.ready_snapshot)
            retired = plan.install_dir.with_name(plan.install_dir.name + ".replaced")
            shutil.rmtree(retired, ignore_errors=True)
            plan.install_dir.rename(retired)
        if is_process:
            assert staged is not None
            staged.rename(plan.install_dir)
            staged = None
        else:
            plan.install_dir.mkdir(parents=True, exist_ok=True)

        if domain:
            assert staged is not None
            for _rel, dest in plan.disks:
                shutil.move(str(staged / dest.name), str(dest))
            shutil.rmtree(staged, ignore_errors=True)
            staged = None
            plan.domain_path.write_text(domain_xml or "")
            _define_with_baseline(domain, plan.domain_path, plan.ready_snapshot)

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
        if is_process:
            seed.update(
                backend="process",
                process_dir=str(plan.install_dir.resolve()),
                heartbeat_timeout=30,
                vm_startup_heartbeat_delay=cfg.vm_startup_heartbeat_delay,
                api_host="127.0.0.1",
            )
        plan.config_path.parent.mkdir(parents=True, exist_ok=True)
        plan.config_path.write_text(yaml.safe_dump(seed, sort_keys=False, allow_unicode=True))
        summary["config_path"] = str(plan.config_path)

        if plan.archive_dir is not None:
            if plan.archive_dir.exists():
                shutil.rmtree(plan.archive_dir)
            plan.archive_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / "conservation" / "archive", plan.archive_dir)

        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)
            retired = None

        summary["imported"] = True
        return summary
    except BaseException:
        # Put the previous install back where it was. The domain is not restored — its
        # snapshot was deleted out of the qcow2 above and cannot be recreated — so the exhibit
        # still needs a re-import; what this saves is the artwork's disk, which is the part a
        # failed import used to take with it.
        if retired is not None:
            shutil.rmtree(plan.install_dir, ignore_errors=True)
            with suppress(OSError):
                retired.rename(plan.install_dir)
                logger.error(
                    "import failed; restored the previous process install at %s"
                    if is_process
                    else "import failed; restored the previous install at %s (the VM must be "
                    "re-imported: its ready snapshot was removed before the failure)",
                    plan.install_dir,
                )
        raise
    finally:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evmctl-import", description="Import a baked artwork zip")
    ap.add_argument("zip", help="the artwork .zip downloaded from the catalog")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, touch nothing")
    ap.add_argument("--force", action="store_true", help="overwrite an existing install")
    ap.add_argument(
        "--vm-startup-heartbeat-delay",
        type=float,
        help="process emulator startup grace in seconds (default: 120)",
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        cfg = ImportConfig(
            **(
                {"vm_startup_heartbeat_delay": args.vm_startup_heartbeat_delay}
                if args.vm_startup_heartbeat_delay is not None
                else {}
            )
        )
        summary = import_bundle(args.zip, cfg, dry_run=args.dry_run, force=args.force)
    except (BundleImportError, ValueError) as e:
        print(f"import refused: {e}", file=sys.stderr)
        return 2
    print(yaml.safe_dump(summary, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
