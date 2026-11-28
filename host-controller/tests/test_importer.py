"""evmc-import: turn a baked artwork zip into a defined libvirt VM on the host."""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from vmctl_core import promotion
from vmctl_core.backends import BackendError, ProcessBackend, ProcessSpec

from vm_controller.config import Config
from vm_controller.importer import (
    BundleImportError,
    ImportConfig,
    _rewrite_domain,
    import_bundle,
    main,
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


def _baked_zip(tmp_path: Path, domain_xml: str = DOMAIN_TMPL) -> Path:
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
    (b / "vm" / "domain.template.xml").write_text(domain_xml)
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


@pytest.mark.parametrize(
    ("vm", "seed", "error"),
    [
        ({"artifact": {"path": "../../x"}}, {}, "outside"),
        ({"profile": "baked-multi", "disks": [{"path": "/etc/x"}]}, {}, "outside"),
        ({"domain_template": "../../x"}, {}, "outside"),
        ({}, {"vm_name": "../x"}, "vm_name"),
        ({}, {"snapshot_name": "../x"}, "snapshot_name"),
        ({}, {"vm_name": "--force"}, "vm_name"),
        ({}, {"snapshot_name": "--force"}, "snapshot_name"),
    ],
)
def test_import_rejects_unsafe_bundle_input(tmp_path, vm, seed, error):
    root = tmp_path / "work"
    (root / "vm").mkdir(parents=True)
    (root / "vm" / "disk.qcow2").write_bytes(b"disk")
    (root / "vm" / "domain.template.xml").write_text(DOMAIN_TMPL)
    (root / "config").mkdir()
    promotion.write_yaml(root / "config" / "exhibition.config.yaml", seed)
    promotion.write_yaml(
        root / promotion.BUNDLE_YAML,
        {"bundle_format": promotion.BUNDLE_FORMAT, "vm": {"profile": "baked", **vm}},
    )
    zpath = tmp_path / "work.zip"
    promotion.pack_bundle_zip(root, zpath)
    runner = FakeRunner()
    with pytest.raises(BundleImportError, match=error):
        import_bundle(zpath, _cfg(tmp_path), run=runner, staging=tmp_path)
    assert runner.calls == []
    assert not (tmp_path / "host").exists()


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
    assert ["virsh", "-c", cfg.libvirt_uri, "define", str(inst / "domain.xml")] in runner.calls
    assert any(cmd[3] == "snapshot-create-as" for cmd in runner.calls)
    # every virsh goes to the libvirt the RUNTIME will look in — without -c, a non-root service
    # user defines into qemu:///session and the runtime never finds the domain (seen on the lab box)
    assert all(cmd[:3] == ["virsh", "-c", "qemu:///system"] for cmd in runner.calls)
    assert summary["commands"] == runner.calls
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
    ppc_xml = DOMAIN_TMPL.replace("<devices>", "<os><type arch='ppc'>hvm</type></os><devices>")
    zpath = _baked_zip(tmp_path, ppc_xml)
    cfg = _cfg(tmp_path)
    runner = FakeRunner()
    summary = import_bundle(zpath, cfg, run=runner, dry_run=True)

    assert summary["dry_run"] is True and summary.get("planned") is True
    create = summary["commands"][-1]
    assert create[3] == "snapshot-create-as" and "--atomic" not in create
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
@pytest.mark.parametrize("injected", [False, True])
def test_force_reimport_tears_down_stale_domain(tmp_path, monkeypatch, injected):
    # HIGH bug 4: force re-import removed the install dir but left the OLD libvirt domain +
    # 'ready' snapshot defined, dangling at deleted paths. The teardown must undefine the domain
    # and drop its snapshots BEFORE redefining.
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)
    uri = cfg.libvirt_uri = "test:///exhibition"
    import_bundle(zpath, cfg, run=FakeRunner())  # first import defines the domain
    domain = Path(cfg.install_root) / "work-1.0.0"
    calls = []

    def run(cmd, **kwargs):
        if injected:
            assert not kwargs
        else:
            assert kwargs["env"]["LC_ALL"] == kwargs["env"]["LANG"] == "C"
            assert kwargs["timeout"] == (120 if cmd[3] == "snapshot-create-as" else 30)
        assert cmd[:3] == ["virsh", "-c", uri]
        if cmd[3] == "destroy":
            assert (domain.with_name(domain.name + ".incoming") / "disk.qcow2").is_file()
        if cmd[3] == "define":
            assert Path(cmd[4]).is_file()
        calls.append(cmd[3:])
        if cmd[3] == "destroy":
            return subprocess.CompletedProcess(cmd, 1, "", "already gone")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setattr(subprocess, "run", run)
    import_bundle(zpath, cfg, run=run if injected else None, force=True)
    vm = "CYF-Work"
    assert calls == [
        ["destroy", vm],
        ["snapshot-delete", vm, "ready", "--children"],
        ["dumpxml", vm],
        ["undefine", vm, "--snapshots-metadata", "--nvram"],
        ["define", str(domain / "domain.xml")],
        ["dumpxml", vm],
        ["snapshot-create-as", vm, "ready", "--description", "evmc import baseline", "--atomic"],
    ]


@_needs_qemu
@pytest.mark.parametrize("timeout", [False, True])
def test_import_fails_closed_when_ready_snapshot_cannot_be_created(tmp_path, timeout):
    # MEDIUM bug 7: a failed 'ready' snapshot create was swallowed into a note yet the import
    # still reported imported:True — an exhibit with no recovery baseline. It must fail instead.
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)

    def run(cmd):
        if cmd[3] == "snapshot-create-as":
            if timeout:
                raise subprocess.TimeoutExpired(cmd, 120)
            return subprocess.CompletedProcess(cmd, 1, "", "no space")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with pytest.raises(BundleImportError, match="recovery baseline"):
        import_bundle(zpath, cfg, run=run)


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
    assert ["virsh", "-c", cfg.libvirt_uri, "define", str(inst / "domain.xml")] in runner.calls


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


# --- bundle-supplied plugin.id is a path (2026-09-08 sweep) -------------------


def _plan_with_plugin_id(tmp_path: Path, plugin_id):
    """A minimal bundle root on disk with the given plugin.id, planned (not installed)."""
    from vm_controller.importer import plan_import

    root = tmp_path / "work-1.0"
    (root / "plugin").mkdir(parents=True)
    (root / "plugin" / "plugin.py").write_text("def setup(r): pass\n")
    promotion.write_yaml(
        root / promotion.BUNDLE_YAML,
        {
            "bundle_format": promotion.BUNDLE_FORMAT,
            "plugin": {"id": plugin_id, "version": "1.0.0", "name": "Work"},
            "artwork": {"id": "work"},
        },
    )
    return plan_import(root, _cfg(tmp_path))


def test_plugin_id_must_be_a_bare_segment(tmp_path):
    """plan_import feeds plugin.id into a directory that install then rmtree's, as root.
    verify_bundle cannot catch a tampered id — the submitter writes CHECKSUMS too."""
    for hostile in ("/etc", "../../../../etc/cron.d", "..", "a/b"):
        with pytest.raises(BundleImportError, match="bare path segment"):
            _plan_with_plugin_id(tmp_path / f"case-{abs(hash(hostile))}", hostile)


def test_a_normal_plugin_id_still_plans(tmp_path):
    plan = _plan_with_plugin_id(tmp_path, "work")
    assert plan.plugin_dir is not None
    assert plan.plugin_dir.name == "work"


# --- a failed force-import must not take the previous exhibit's disk with it ---


@_needs_qemu
def test_force_import_restores_the_previous_install_when_the_import_fails(tmp_path):
    """The old sequence destroyed first and copied second, so the likeliest failure — no room
    for the new disk — landed after the running exhibit's image was already deleted. Disks are
    staged beside the old install now, and a failure after the swap puts the old one back."""
    zpath = _baked_zip(tmp_path)
    cfg = _cfg(tmp_path)

    first = import_bundle(zpath, cfg, run=FakeRunner())
    assert first["imported"] is True
    install = Path(first["install_dir"])
    marker = (install / "disk.qcow2").read_bytes()
    (install / "witness").write_text("the exhibit that was already here")

    class FailingDefine(FakeRunner):
        def __call__(self, cmd):
            self.calls.append(cmd)
            if "define" in cmd:
                raise subprocess.CalledProcessError(1, cmd, "", "libvirt says no")
            return subprocess.CompletedProcess(cmd, 0, "", "")

    with pytest.raises(BackendError, match="libvirt says no"):
        import_bundle(zpath, cfg, run=FailingDefine(), force=True)

    assert install.is_dir(), "the previous install was not restored"
    assert (install / "witness").read_text() == "the exhibit that was already here"
    assert (install / "disk.qcow2").read_bytes() == marker
    assert not install.with_name(install.name + ".replaced").exists()
    assert not install.with_name(install.name + ".incoming").exists()


@_needs_qemu
def test_import_and_runtime_agree_on_which_libvirt(tmp_path):
    """The importer's default libvirt MUST be the runtime controller's default.

    These are two halves of one system that never talk to each other: the importer defines a
    domain, the runtime starts it by name. If their URIs differ, nothing errors — the import
    reports success, and the runtime reports "failed to get domain" about a domain that exists,
    in the other libvirt. That is what happened on the lab box: the import landed in
    qemu:///session while every artwork on that host lives in qemu:///system."""
    import inspect

    from vm_controller.vm_manager import VMManager

    runtime_default = inspect.signature(VMManager.__init__).parameters["uri"].default
    assert ImportConfig(_env_file=None).libvirt_uri == runtime_default


@pytest.fixture
def process_zip(tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError(f"unexpected shell-out: {args}")

    monkeypatch.setattr(subprocess, "run", forbid)
    instance = tmp_path / "source"
    (instance / "images").mkdir(parents=True)
    (instance / "images" / "disk.img").write_bytes(b"pristine")
    media = instance / "emulator.sh"
    media.write_text("#!/bin/sh\nexec sleep 60\n")
    media.chmod(0o755)
    spec = ProcessSpec(
        argv=["../emulator.sh"],
        cwd="work",
        images=["images/disk.img"],
        media={"emulator.sh": str(media)},
        media_sha256={"emulator.sh": promotion.sha256_file(media)},
    )
    (instance / "process.json").write_text(spec.model_dump_json())
    bundle = promotion.create_process_bundle(instance, tmp_path / "process-1.0", provenance={})
    promotion.unseal_bundle(bundle)
    (bundle / "config").mkdir()
    promotion.write_yaml(
        bundle / "config" / "exhibition.config.yaml",
        {"vm_name": "Emulator", "heartbeat_timeout": 1, "api_host": "0.0.0.0"},
    )
    promotion.write_checksums(bundle)
    zpath = tmp_path / "process.zip"
    promotion.pack_bundle_zip(bundle, zpath)
    return zpath


@pytest.mark.parametrize("with_plugin", [False, True])
def test_process_import_materializes_instance_and_baseline(tmp_path, process_zip, with_plugin):
    if with_plugin:
        bundle = tmp_path / "process-1.0"
        (bundle / "plugin").mkdir()
        (bundle / "plugin" / "plugin.py").write_text("def setup(r): pass\n")
        manifest = promotion.read_yaml(bundle / promotion.BUNDLE_YAML)
        manifest["plugin"] = {"id": "emulator"}
        promotion.write_yaml(bundle / promotion.BUNDLE_YAML, manifest)
        promotion.write_checksums(bundle)
        promotion.pack_bundle_zip(bundle, process_zip)
    cfg = _cfg(tmp_path)
    runner = FakeRunner()
    summary = import_bundle(process_zip, cfg, run=runner)
    assert summary["imported"] is True
    assert summary["commands"] == runner.calls == []
    install = Path(summary["install_dir"])
    assert install == Path(cfg.install_root) / "process-1.0"
    assert not (install / "domain.xml").exists()
    assert (install / "work").is_dir()
    assert (install / "emulator.sh").stat().st_mode & 0o111
    assert (install / "images" / "disk.img").read_bytes() == b"pristine"
    backend = ProcessBackend("Emulator", instance_dir=install)
    assert backend.snapshot_list() == ["ready"]
    (install / "images" / "disk.img").write_bytes(b"changed")
    backend.snapshot_revert("ready")
    assert (install / "images" / "disk.img").read_bytes() == b"pristine"
    written = Config.from_yaml(Path(summary["config_path"]))
    assert written.backend == "process"
    assert written.process_dir == str(install.resolve())
    assert written.vm_name == "Emulator"
    assert written.snapshot_name == "ready"
    assert written.api_host == "127.0.0.1"
    assert written.heartbeat_timeout == 30
    assert written.vm_startup_heartbeat_delay == 120
    assert (Path(cfg.plugins_dir) / "emulator" / "plugin.py").is_file() == with_plugin


@pytest.mark.parametrize(
    "probe",
    [
        {"kind": "amiga", "kickstart": "roms/kick.rom", "expect": ["SYS:Art"], "max_age_s": 17.5},
        {"kind": "mac68k", "ram_bytes": 8388608, "expect": ["ArtWork"], "max_age_s": 23},
    ],
)
def test_process_import_preserves_probe_settings_verbatim(tmp_path, process_zip, probe):
    bundle = tmp_path / "process-1.0"
    definition = bundle / "vm" / "process.json"
    spec = json.loads(definition.read_text())
    spec["probe"] = probe
    definition.write_text(json.dumps(spec, indent=3) + "\n")
    promotion.write_checksums(bundle)
    promotion.pack_bundle_zip(bundle, process_zip)

    summary = import_bundle(process_zip, _cfg(tmp_path))
    install = Path(summary["install_dir"])
    assert (install / "process.json").read_bytes() == definition.read_bytes()
    assert json.loads((install / "process.json").read_text())["probe"] == probe
    backend = ProcessBackend("Emulator", instance_dir=install)
    assert backend.spec.probe is not None
    assert backend.spec.probe.model_dump(include=set(probe)) == probe


def test_process_import_refuses_existing_without_stopping(tmp_path, process_zip):
    cfg = _cfg(tmp_path)
    summary = import_bundle(process_zip, cfg)
    backend = ProcessBackend("Emulator", instance_dir=summary["install_dir"])
    backend.start()
    try:
        with pytest.raises(BundleImportError, match="already exists"):
            import_bundle(process_zip, cfg)
        assert backend.is_running()
    finally:
        backend.stop()


def test_process_force_reimport_stops_before_replacing(tmp_path, process_zip, monkeypatch):
    cfg = _cfg(tmp_path)
    summary = import_bundle(process_zip, cfg)
    install = Path(summary["install_dir"])
    backend = ProcessBackend("Emulator", instance_dir=install)
    (install / "witness").write_text("old instance")
    (install / "images" / "disk.img").write_bytes(b"changed")
    backend.start()
    stop = ProcessBackend.stop
    stopped = []

    def checked_stop(self):
        assert self.instance_dir == install
        assert (install / "witness").is_file()
        assert self.is_running()
        stop(self)
        assert not self.is_running()
        stopped.append(True)

    monkeypatch.setattr(ProcessBackend, "stop", checked_stop)
    try:
        assert import_bundle(process_zip, cfg, force=True)["imported"]
        assert stopped == [True]
        assert not backend.is_running()
        assert not (install / "witness").exists()
        assert (install / "images" / "disk.img").read_bytes() == b"pristine"
        assert backend.snapshot_list() == ["ready"]
        assert not install.with_name(install.name + ".incoming").exists()
        assert not install.with_name(install.name + ".replaced").exists()
    finally:
        stop(backend)


def test_process_import_refuses_tampered_bundle(tmp_path, process_zip):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(process_zip) as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("disk.img"):
                data += b"tampered"
            zout.writestr(item, data)
    cfg = _cfg(tmp_path)
    with pytest.raises(BundleImportError, match="verification"):
        import_bundle(bad, cfg)
    assert not Path(cfg.install_root).exists()
    assert not Path(cfg.config_dir).exists()


def test_process_import_dry_run(tmp_path, process_zip):
    cfg = _cfg(tmp_path)
    summary = import_bundle(process_zip, cfg, dry_run=True)
    assert summary["planned"]
    assert summary["commands"] == []
    assert not Path(cfg.install_root).exists()
    assert not Path(cfg.config_dir).exists()


def test_process_snapshot_failure_keeps_running_install(tmp_path, process_zip, monkeypatch):
    cfg = _cfg(tmp_path)
    summary = import_bundle(process_zip, cfg)
    backend = ProcessBackend("Emulator", instance_dir=summary["install_dir"])
    backend.start()

    def fail_snapshot(self, name):
        raise OSError("no space")

    monkeypatch.setattr(ProcessBackend, "snapshot_create", fail_snapshot)
    try:
        with pytest.raises(OSError, match="no space"):
            import_bundle(process_zip, cfg, force=True)
        assert backend.is_running()
        assert backend.snapshot_list() == ["ready"]
        install = backend.instance_dir
        assert not install.with_name(install.name + ".incoming").exists()
    finally:
        backend.stop()


def test_process_cli_startup_delay(tmp_path, process_zip, monkeypatch):
    cfg = _cfg(tmp_path)
    for name in ("install_root", "config_dir", "plugins_dir", "archive_dir"):
        monkeypatch.setenv(f"VMCTL_IMPORT_{name.upper()}", getattr(cfg, name))
    assert main([str(process_zip), "--vm-startup-heartbeat-delay", "180"]) == 0
    written = Config.from_yaml(Path(cfg.config_dir) / "process-1.0.yaml")
    assert written.vm_startup_heartbeat_delay == 180
