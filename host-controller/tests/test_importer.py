"""evmc-import: turn a baked artwork zip into a defined libvirt VM on the host."""

import shutil
import subprocess
from pathlib import Path

import pytest
from vmctl_core import promotion

from vm_controller.importer import (
    BundleImportError,
    ImportConfig,
    _rewrite_domain,
    import_bundle,
)

_needs_qemu = pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not installed")

DOMAIN_TMPL = """<domain type='kvm'>
  <name>orig-name</name>
  <memory unit='MiB'>512</memory>
  <devices>
    <disk type='file' device='disk'>
      <source file='/rvmc/drives/artwork/overlay.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
  </devices>
</domain>
"""


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _baked_zip(tmp_path: Path) -> Path:
    """Simulate: rvmc promotes thin -> catalog bakes -> a downloaded baked zip."""
    cat_dir = tmp_path / "bases"
    cat_dir.mkdir()
    base = cat_dir / "g.qcow2"
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", str(base), "64M"], check=True, capture_output=True
    )
    (cat_dir / "catalog.yaml").write_text("bases:\n  - id: g\n    path: g.qcow2\n    os: linux\n")
    base_sha = promotion.sha256_file(base)

    b = tmp_path / "drive" / "work-1.0.0"
    (b / "plugin").mkdir(parents=True)
    (b / "plugin" / "manifest.yaml").write_text("id: work\nversion: 1.0.0\n")
    (b / "plugin" / "plugin.py").write_text("def setup(r): pass\n")
    (b / "vm").mkdir()
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(base),
            "-F",
            "qcow2",
            str(b / "vm" / "overlay.qcow2"),
        ],
        check=True,
        capture_output=True,
    )
    (b / "vm" / "domain.template.xml").write_text(DOMAIN_TMPL)
    (b / "config").mkdir()
    promotion.write_yaml(
        b / "config" / "exhibition.config.yaml",
        {"vm_name": "CYF-Work", "snapshot_name": "ready", "plugins": ["work"]},
    )
    promotion.write_yaml(
        b / "vm" / "base.ref.yaml",
        {"id": "g", "name": "G", "path": "g.qcow2", "os": "linux", "sha256": base_sha},
    )
    promotion.write_yaml(
        b / promotion.BUNDLE_YAML,
        {
            "bundle_format": promotion.BUNDLE_FORMAT,
            "plugin": {"id": "work", "version": "1.0.0", "name": "Work"},
            "vm": {
                "profile": "thin",
                "base": {"id": "g", "included": False},
                "artifact": {
                    "kind": "overlay",
                    "path": "vm/overlay.qcow2",
                    "ready_snapshot": "ready",
                },
            },
        },
    )
    promotion.write_checksums(b)

    zpath = tmp_path / "artwork.zip"
    promotion.pack_bundle_zip(b, zpath, catalog_path=cat_dir / "catalog.yaml")
    return zpath


def _cfg(tmp_path: Path) -> ImportConfig:
    root = tmp_path / "host"
    return ImportConfig(
        install_root=str(root / "artworks"),
        config_dir=str(root / "config"),
        plugins_dir=str(root / "plugins"),
        archive_dir=str(root / "archive"),
    )


# --- domain rewrite (pure) ----------------------------------------------------


def test_rewrite_domain_repoints_disk_and_name():
    out = _rewrite_domain(DOMAIN_TMPL, vm_name="CYF-Work", disk_paths=[Path("/host/disk.qcow2")])
    assert "<name>CYF-Work</name>" in out
    assert 'file="/host/disk.qcow2"' in out or "file='/host/disk.qcow2'" in out
    assert "overlay.qcow2" not in out


def test_rewrite_domain_refuses_disk_count_mismatch():
    two = DOMAIN_TMPL.replace(
        "    </disk>\n",
        "    </disk>\n    <disk type='file' device='disk'>"
        "<source file='/x/aux.qcow2'/><target dev='vdb'/></disk>\n",
        1,
    )
    # 2-disk domain but only 1 baked disk provided -> refuse to mis-wire
    with pytest.raises(BundleImportError, match="refusing to mis-wire"):
        _rewrite_domain(two, vm_name="x", disk_paths=[Path("/host/disk.qcow2")])


def test_rewrite_domain_multi_disk_repoints_all_in_order():
    two = DOMAIN_TMPL.replace(
        "    </disk>\n",
        "    </disk>\n    <disk type='file' device='disk'>"
        "<source file='/x/aux.qcow2'/><target dev='vdb'/></disk>\n",
        1,
    )
    out = _rewrite_domain(
        two, vm_name="Multi", disk_paths=[Path("/host/disk-0.qcow2"), Path("/host/disk-1.qcow2")]
    )
    assert "/host/disk-0.qcow2" in out and "/host/disk-1.qcow2" in out
    assert "overlay.qcow2" not in out and "aux.qcow2" not in out


def test_rewrite_domain_refuses_qemu_arg_disk():
    q = """<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>x</name>
  <qemu:commandline>
    <qemu:arg value='file=/x/main.qcow2,format=qcow2,media=disk'/>
  </qemu:commandline>
</domain>"""
    with pytest.raises(BundleImportError, match="disk-over-commandline"):
        _rewrite_domain(q, vm_name="x", disk_paths=[Path("/host/disk.qcow2")])


def test_rewrite_domain_on_real_rvmc_domain():
    # validate against a domain the rvmc renderer actually produces, not a synthetic
    # one: the standard branch carries <driver> + <target> + a separate cdrom disk.
    from vmctl_core import clone
    from vmctl_core.catalog import BaseImage

    base = BaseImage(id="winxp-golden", name="WinXP", path="winxp.qcow2", os="windows-xp")
    xml = clone.render_domain_xml(
        base, name="orig-name", disk_path=Path("/rvmc/drives/x/overlay.qcow2")
    )
    out = _rewrite_domain(xml, vm_name="CYF-Real", disk_paths=[Path("/host/artworks/x/disk.qcow2")])
    assert "<name>CYF-Real</name>" in out
    assert "/host/artworks/x/disk.qcow2" in out
    assert "overlay.qcow2" not in out  # the only device='disk' source was repointed
    assert "device='cdrom'" in out or 'device="cdrom"' in out  # cdrom untouched
    assert "type='qcow2'" in out or 'type="qcow2"' in out  # driver preserved


def test_rewrite_domain_macos_multidisk_preserves_firmware():
    # a real macOS x86 domain: OpenCore aux disk + main + OVMF <loader>. Both disks
    # repoint; the firmware line and any qemu:commandline are preserved.
    from vmctl_core import clone
    from vmctl_core.catalog import AuxDisk, BaseImage, Hardware

    hw = Hardware(
        macos=True,
        loader="/usr/share/OVMF/OVMF_CODE.fd",
        aux_disks=[AuxDisk(path="/goldens/opencore.qcow2", bus="sata")],
    )
    base = BaseImage(
        id="sl", name="Snow Leopard", path="/goldens/macos.qcow2", os="macos-10.6", hw=hw
    )
    xml = clone.render_domain_xml(base, name="sl-src", disk_path=Path("/rvmc/main.qcow2"))
    out = _rewrite_domain(
        xml, vm_name="SL", disk_paths=[Path("/host/disk-0.qcow2"), Path("/host/disk-1.qcow2")]
    )
    assert "/host/disk-0.qcow2" in out and "/host/disk-1.qcow2" in out
    assert "OVMF" in out  # firmware loader preserved
    assert "/rvmc/main.qcow2" not in out


# --- full import --------------------------------------------------------------


@_needs_qemu
def test_import_materializes_everything(tmp_path):
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)
    runner = FakeRunner()
    summary = import_bundle(zpath, cfg, run=runner)

    assert summary["imported"] is True
    assert summary["vm_name"] == "CYF-Work"
    inst = Path(cfg.install_root) / "work-1.0.0"
    # disk placed + standalone
    assert (inst / "disk.qcow2").is_file()
    info = subprocess.run(
        ["qemu-img", "info", str(inst / "disk.qcow2")], check=True, capture_output=True, text=True
    ).stdout
    assert "backing file" not in info
    # domain defined + snapshot created via libvirt
    assert ["virsh", "define", str(inst / "domain.xml")] in runner.calls
    assert any(c[:2] == ["virsh", "snapshot-create-as"] and "CYF-Work" in c for c in runner.calls)
    # domain xml points at the local disk, not the rvmc overlay
    dxml = (inst / "domain.xml").read_text()
    assert str(inst / "disk.qcow2") in dxml and "overlay.qcow2" not in dxml
    # plugin installed
    assert (Path(cfg.plugins_dir) / "work" / "plugin.py").is_file()
    # per-artwork config written with resolved names
    written = promotion.read_yaml(Path(cfg.config_dir) / "work-1.0.0.yaml")
    assert written["vm_name"] == "CYF-Work" and written["snapshot_name"] == "ready"


@_needs_qemu
def test_import_dry_run_touches_nothing(tmp_path):
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)
    runner = FakeRunner()
    summary = import_bundle(zpath, cfg, run=runner, dry_run=True)

    assert summary["dry_run"] is True and summary.get("planned") is True
    assert runner.calls == []  # no libvirt touched
    assert not Path(cfg.install_root).exists()


@_needs_qemu
def test_import_refuses_existing_without_force(tmp_path):
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)
    import_bundle(zpath, cfg, run=FakeRunner())
    with pytest.raises(BundleImportError, match="already exists"):
        import_bundle(zpath, cfg, run=FakeRunner())
    # force re-imports cleanly
    summary = import_bundle(zpath, cfg, run=FakeRunner(), force=True)
    assert summary["imported"] is True


@_needs_qemu
def test_force_reimport_tears_down_stale_domain(tmp_path):
    # HIGH bug 4: force re-import removed the install dir but left the OLD libvirt domain +
    # 'ready' snapshot defined, dangling at deleted paths. The teardown must undefine the domain
    # and drop its snapshots BEFORE redefining.
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)
    import_bundle(zpath, cfg, run=FakeRunner())  # first import defines the domain

    runner = FakeRunner()
    import_bundle(zpath, cfg, run=runner, force=True)
    subs = [tuple(c[:2]) for c in runner.calls]

    assert ("virsh", "undefine") in subs
    assert ("virsh", "snapshot-delete") in subs
    assert ("virsh", "destroy") in subs
    # teardown happens before the domain is (re)defined
    assert subs.index(("virsh", "undefine")) < subs.index(("virsh", "define"))


@_needs_qemu
def test_import_fails_closed_when_ready_snapshot_cannot_be_created(tmp_path):
    # MEDIUM bug 7: a failed 'ready' snapshot create was swallowed into a note yet the import
    # still reported imported:True — an exhibit with no recovery baseline. It must fail instead.
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)

    class _SnapFails(FakeRunner):
        def __call__(self, cmd):
            self.calls.append(cmd)
            if cmd[:2] == ["virsh", "snapshot-create-as"]:
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="no space")
            return subprocess.CompletedProcess(cmd, 0, "", "")

    with pytest.raises(BundleImportError, match="recovery baseline"):
        import_bundle(zpath, cfg, run=_SnapFails())


def _baked_multi_zip(tmp_path: Path) -> Path:
    """A macOS-style thin bundle (OpenCore aux + main) baked to a multi-disk zip."""
    from vmctl_core import clone
    from vmctl_core.catalog import AuxDisk, BaseImage, Hardware

    cat_dir = tmp_path / "bases"
    cat_dir.mkdir()
    main_golden = cat_dir / "macos.qcow2"
    oc_golden = cat_dir / "opencore.qcow2"
    for g in (main_golden, oc_golden):
        subprocess.run(
            ["qemu-img", "create", "-f", "qcow2", str(g), "64M"], check=True, capture_output=True
        )
    (cat_dir / "catalog.yaml").write_text(
        f"bases:\n  - id: sl\n    path: {main_golden}\n    os: macos-10.6\n"
    )
    main_sha = promotion.sha256_file(main_golden)
    oc_sha = promotion.sha256_file(oc_golden)

    b = tmp_path / "drive" / "sl-1.0.0"
    (b / "plugin").mkdir(parents=True)
    (b / "plugin" / "manifest.yaml").write_text("id: sl\nversion: 1.0.0\n")
    (b / "vm").mkdir()
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(main_golden),
            "-F",
            "qcow2",
            str(b / "vm" / "overlay.qcow2"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(oc_golden),
            "-F",
            "qcow2",
            str(b / "vm" / "aux-0.qcow2"),
        ],
        check=True,
        capture_output=True,
    )

    hw = Hardware(
        macos=True,
        loader="/usr/share/OVMF/OVMF_CODE.fd",
        aux_disks=[AuxDisk(path=str(oc_golden), bus="sata")],
    )
    base = BaseImage(id="sl", name="Snow Leopard", path=str(main_golden), os="macos-10.6", hw=hw)
    dom = clone.render_domain_xml(base, name="sl-src", disk_path=b / "vm" / "overlay.qcow2")
    (b / "vm" / "domain.template.xml").write_text(dom)

    (b / "config").mkdir()
    promotion.write_yaml(
        b / "config" / "exhibition.config.yaml",
        {"vm_name": "CYF-SL", "snapshot_name": "ready", "plugins": ["sl"]},
    )
    promotion.write_yaml(
        b / "vm" / "base.ref.yaml",
        {"id": "sl", "path": str(main_golden), "os": "macos-10.6", "sha256": main_sha},
    )
    promotion.write_yaml(
        b / promotion.BUNDLE_YAML,
        {
            "bundle_format": promotion.BUNDLE_FORMAT,
            "plugin": {"id": "sl", "version": "1.0.0", "name": "Snow Leopard"},
            "vm": {
                "profile": "thin",
                "base": {"id": "sl", "included": False},
                "artifact": {
                    "kind": "overlay",
                    "path": "vm/overlay.qcow2",
                    "ready_snapshot": "ready",
                },
                "aux_disks": [
                    {
                        "overlay": "vm/aux-0.qcow2",
                        "base_path": str(oc_golden),
                        "base_sha": oc_sha,
                        "bus": "sata",
                        "boot": 1,
                    }
                ],
                "host_requires": ["/usr/share/OVMF/OVMF_CODE.fd"],
            },
        },
    )
    promotion.write_checksums(b)

    zpath = tmp_path / "sl.zip"
    s = promotion.pack_bundle_zip(b, zpath, catalog_path=cat_dir / "catalog.yaml")
    assert s["profile"] == "baked-multi" and len(s["disk_sha256s"]) == 2
    return zpath


@_needs_qemu
def test_import_macos_multidisk(tmp_path):
    zpath = _baked_multi_zip(tmp_path)
    cfg = _cfg(tmp_path)
    runner = FakeRunner()
    summary = import_bundle(zpath, cfg, run=runner)

    assert summary["imported"] is True and summary["profile"] == "baked-multi"
    inst = Path(cfg.install_root) / "sl-1.0.0"
    # both baked disks placed, standalone
    assert (inst / "disk-0.qcow2").is_file() and (inst / "disk-1.qcow2").is_file()
    for d in ("disk-0.qcow2", "disk-1.qcow2"):
        info = subprocess.run(
            ["qemu-img", "info", str(inst / d)], check=True, capture_output=True, text=True
        ).stdout
        assert "backing file" not in info
    # domain rewired to both local disks + firmware preserved
    dxml = (inst / "domain.xml").read_text()
    assert str(inst / "disk-0.qcow2") in dxml and str(inst / "disk-1.qcow2") in dxml
    assert "OVMF" in dxml
    # OVMF isn't on this test host -> surfaced as a host-prereq note
    assert any("OVMF" in n for n in summary.get("notes", []))
    assert ["virsh", "define", str(inst / "domain.xml")] in runner.calls


@_needs_qemu
def test_import_refuses_tampered_bundle(tmp_path):
    import zipfile

    zpath = _baked_zip(tmp_path)
    # flip a byte in the manifest inside the zip -> checksum mismatch
    tampered = tmp_path / "bad.zip"
    with zipfile.ZipFile(zpath) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename.endswith("plugin/plugin.py"):
                data = data + b"# EVIL\n"
            zout.writestr(it, data)
    with pytest.raises(BundleImportError, match="verification"):
        import_bundle(tampered, cfg=_cfg(tmp_path), run=FakeRunner())
