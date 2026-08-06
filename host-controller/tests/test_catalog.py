"""evmc-catalog: scans the shared drives, verifies bundles, serves baked zips."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from vmctl_core import promotion

from vm_controller.catalog import CatalogConfig, _resolve_bundle, create_app, scan

_needs_qemu = pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img not installed")

# every test builds a real qcow2 golden + overlay for the bake path
pytestmark = _needs_qemu


def _golden_catalog(tmp_path: Path) -> tuple[Path, str]:
    cat_dir = tmp_path / "bases"
    cat_dir.mkdir()
    base = cat_dir / "g.qcow2"
    subprocess.run(
        ["qemu-img", "create", "-f", "qcow2", str(base), "64M"], check=True, capture_output=True
    )
    (cat_dir / "catalog.yaml").write_text("bases:\n  - id: g\n    path: g.qcow2\n    os: linux\n")
    return cat_dir / "catalog.yaml", promotion.sha256_file(base)


def _promote_thin(drives: Path, project: str, base_path: Path, base_sha: str) -> Path:
    """Simulate rvmc `promote` writing a thin bundle to <drive>/exhibition/."""
    bdir = drives / project / "exhibition" / "work-1.0.0"
    (bdir / "plugin").mkdir(parents=True)
    (bdir / "plugin" / "manifest.yaml").write_text("id: work\nversion: 1.0.0\n")
    (bdir / "vm").mkdir()
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-b",
            str(base_path),
            "-F",
            "qcow2",
            str(bdir / "vm" / "overlay.qcow2"),
        ],
        check=True,
        capture_output=True,
    )
    (bdir / "vm" / "domain.template.xml").write_text("<domain/>\n")
    promotion.write_yaml(
        bdir / "vm" / "base.ref.yaml",
        {"id": "g", "name": "G", "path": "g.qcow2", "os": "linux", "sha256": base_sha},
    )
    promotion.write_yaml(
        bdir / promotion.PROVENANCE,
        {"origin_project": project, "exported_by": "marc", "exported_at": "2026-07-29T10:00:00"},
    )
    promotion.write_yaml(
        bdir / promotion.BUNDLE_YAML,
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
    promotion.write_checksums(bdir)
    return bdir


@pytest.fixture
def env(tmp_path):
    drives = tmp_path / "drives"
    catalog_path, base_sha = _golden_catalog(tmp_path)
    _promote_thin(drives, "artwork-alpha", catalog_path.parent / "g.qcow2", base_sha)
    cfg = CatalogConfig(drives_root=str(drives), catalog_path=str(catalog_path))
    return cfg, drives, base_sha


def test_scan_finds_promoted_bundle(env):
    cfg, drives, _ = env
    items = scan(cfg.drives_root, deep=True)
    assert len(items) == 1
    e = items[0]
    assert e["id"] == "artwork-alpha/work-1.0.0"
    assert e["plugin"]["id"] == "work"
    assert e["vm_profile"] == "thin"
    assert e["verified"] is True
    assert e["provenance"]["exported_by"] == "marc"


def test_list_endpoint(env):
    cfg, *_ = env
    c = TestClient(create_app(cfg))
    r = c.get("/api/v1/catalog")
    assert r.status_code == 200
    assert r.json()["bundles"][0]["plugin"]["version"] == "1.0.0"


def test_download_bakes_thin_bundle(env, tmp_path):
    cfg, drives, base_sha = env
    c = TestClient(create_app(cfg))
    r = c.get("/api/v1/catalog/artwork-alpha/work-1.0.0/download.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    zpath = tmp_path / "dl.zip"
    zpath.write_bytes(r.content)
    root = promotion.unpack_bundle_zip(zpath, tmp_path / "imp")
    # self-contained baked disk: no golden, no overlay travel
    assert (root / "vm" / "disk.qcow2").is_file()
    assert not (root / "vm" / "base.qcow2").exists()
    assert not (root / "vm" / "overlay.qcow2").exists()
    v = promotion.verify_bundle(root)
    assert v["ok"] is True, v
    by = promotion.read_yaml(root / promotion.BUNDLE_YAML)
    assert by["vm"]["profile"] == "baked" and by["vm"]["artifact"]["kind"] == "disk"

    # source bundle on the drive is untouched (still thin: overlay present, no disk)
    src = drives / "artwork-alpha" / "exhibition" / "work-1.0.0" / "vm"
    assert (src / "overlay.qcow2").exists() and not (src / "disk.qcow2").exists()


def test_download_missing_bundle_404(env):
    cfg, *_ = env
    c = TestClient(create_app(cfg))
    assert c.get("/api/v1/catalog/artwork-alpha/nope-9.9.9/download.zip").status_code == 404


def test_resolve_rejects_traversal(env):
    cfg, *_ = env
    with pytest.raises(Exception):
        _resolve_bundle(cfg, "..", "work-1.0.0")


def test_index_page_renders(env):
    cfg, *_ = env
    c = TestClient(create_app(cfg))
    r = c.get("/")
    assert r.status_code == 200 and "finished artworks" in r.text
    assert "download .zip" in r.text
