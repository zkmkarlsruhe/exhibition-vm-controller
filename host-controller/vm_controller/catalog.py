"""evmc-catalog — the "done" shelf (the research/29 §8 handoff, in-system).

This is the exhibition end of the same trick autopsy<->rvmc uses: instead of
pushing files over the network between machines, the service is rooted at the
SAME shared ``drives/`` filesystem the research controller (rvmc) writes to.

When a researcher promotes finished work, rvmc drops a checksum-signed bundle at
``<drive>/exhibition/<plugin-id>-<version>/`` (format owned by
``vmctl_core.promotion``). This catalog scans every drive's ``exhibition/`` dir,
verifies each bundle, and presents the finished artworks as a browsable shelf on
vmctl.org. The handoff is in-system: the moment promotion writes the bundle, it
appears here — nothing is pushed anywhere.

Each entry downloads as ONE self-contained (fat) zip: a thin bundle (overlay +
base ref, the drive default) is fattened on the way out by resolving the golden
base from the catalog. A field exhibition host then runs ``evmctl import
<zip>`` to materialize everything. This module is READ-ONLY over the drives root
— it never mutates a bundle.

Runs as its own service/port, decoupled from the single-VM runtime controller
(``vm_controller.api``): the catalog role has no VM of its own.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.background import BackgroundTask
from vmctl_core import promotion

logger = logging.getLogger(__name__)


class CatalogConfig(BaseSettings):
    """Catalog-role settings. Separate from the runtime ``Config`` (no ``vm_name``)."""

    model_config = SettingsConfigDict(
        env_prefix="VMCTL_CATALOG_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    drives_root: str = Field(
        default="/drives",
        description="Shared drives root rvmc writes bundles under (<drive>/exhibition/...)",
    )
    catalog_path: str = Field(
        default="",
        description="Golden-base catalog.yaml — resolves the base when fattening a thin bundle",
    )
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8090)
    log_level: str = Field(default="INFO")


EXHIBITION_DIRNAME = "exhibition"


def _safe_component(name: str) -> str:
    """Reject path separators / traversal in a URL-derived path component."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise HTTPException(status_code=400, detail=f"invalid path component: {name!r}")
    return name


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def bundle_entry(drives_root: Path, project: str, bdir: Path, *, deep: bool = False) -> dict:
    """One shelf entry: identity + verify summary + provenance from a bundle dir."""
    by = promotion.read_yaml(bdir / promotion.BUNDLE_YAML)
    prov: dict = {}
    pf = bdir / promotion.PROVENANCE
    if pf.is_file():
        prov = promotion.read_yaml(pf)
    plugin = by.get("plugin") or {}
    vm = by.get("vm") or {}
    entry = {
        "id": f"{project}/{bdir.name}",
        "project": project,
        "bundle": bdir.name,
        "plugin": {
            "id": plugin.get("id"),
            "version": plugin.get("version"),
            "name": plugin.get("name"),
        },
        "has_vm": bool(vm),
        "vm_profile": vm.get("profile") if vm else None,
        "conservation": bool((by.get("conservation") or {}).get("included")),
        "provenance": {
            "origin_project": prov.get("origin_project"),
            "exported_at": prov.get("exported_at"),
            "exported_by": prov.get("exported_by"),
            "git_commit": prov.get("git_commit"),
        },
    }
    if deep:
        v = promotion.verify_bundle(bdir)
        entry["verified"] = v["ok"]
        entry["verify"] = {k: v[k] for k in ("mismatched", "missing", "extra", "unsafe")}
        entry["size_bytes"] = _dir_size(bdir)
    return entry


def scan(drives_root: str | Path, *, deep: bool = False) -> list[dict]:
    """Every promoted bundle across all drives, sorted by (project, bundle).

    ``deep`` re-hashes each bundle (verify + size) — accurate but heavier; the
    listing uses shallow, the per-item detail uses deep.
    """
    root = Path(drives_root)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for drive in sorted(p for p in root.iterdir() if p.is_dir()):
        exdir = drive / EXHIBITION_DIRNAME
        if not exdir.is_dir():
            continue
        for bdir in sorted(p for p in exdir.iterdir() if p.is_dir()):
            if not (bdir / promotion.BUNDLE_YAML).is_file():
                continue
            try:
                out.append(bundle_entry(root, drive.name, bdir, deep=deep))
            except Exception as e:  # a malformed bundle must not sink the whole shelf
                logger.warning("skipping unreadable bundle %s: %s", bdir, e)
                out.append(
                    {
                        "id": f"{drive.name}/{bdir.name}",
                        "project": drive.name,
                        "bundle": bdir.name,
                        "error": str(e),
                    }
                )
    return out


def _resolve_bundle(cfg: CatalogConfig, project: str, bundle: str) -> Path:
    """Map (project, bundle) to a bundle dir, confined under drives_root."""
    _safe_component(project)
    _safe_component(bundle)
    root = Path(cfg.drives_root).resolve()
    bdir = (root / project / EXHIBITION_DIRNAME / bundle).resolve()
    if not bdir.is_relative_to(root) or not (bdir / promotion.BUNDLE_YAML).is_file():
        raise HTTPException(status_code=404, detail=f"no such bundle: {project}/{bundle}")
    return bdir


def create_app(cfg: CatalogConfig | None = None) -> FastAPI:
    cfg = cfg or CatalogConfig()
    app = FastAPI(title="evmc-catalog", version="1.0.0")

    @app.get("/api/v1/catalog")
    def list_catalog(deep: bool = False):
        """The done shelf: every promoted bundle on the shared drives."""
        return {"drives_root": cfg.drives_root, "bundles": scan(cfg.drives_root, deep=deep)}

    @app.get("/api/v1/catalog/{project}/{bundle}")
    def bundle_detail(project: str, bundle: str):
        bdir = _resolve_bundle(cfg, project, bundle)
        return bundle_entry(Path(cfg.drives_root).resolve(), project, bdir, deep=True)

    @app.get("/api/v1/catalog/{project}/{bundle}/download.zip")
    def download(project: str, bundle: str):
        """Stream the artwork as ONE self-contained (fat) zip for a field host."""
        bdir = _resolve_bundle(cfg, project, bundle)
        v = promotion.verify_bundle(bdir)
        if not v["ok"]:
            raise HTTPException(status_code=409, detail={"error": "bundle fails verify", **v})
        workdir = Path(tempfile.mkdtemp(prefix="evmc-catalog-"))
        zpath = workdir / f"{bundle}.zip"
        try:
            promotion.pack_bundle_zip(bdir, zpath, catalog_path=cfg.catalog_path or None)
        except Exception as e:
            shutil.rmtree(workdir, ignore_errors=True)
            # thin bundle + no/again unresolved base -> 409, not a 500
            raise HTTPException(status_code=409, detail=f"cannot pack bundle: {e}") from e
        return FileResponse(
            zpath,
            media_type="application/zip",
            filename=f"{bundle}.zip",
            background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
        )

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _render_shelf(scan(cfg.drives_root, deep=True))

    return app


def _render_shelf(bundles: list[dict]) -> str:
    rows = []
    for b in bundles:
        if b.get("error"):
            rows.append(
                f"<tr class='bad'><td>{b['id']}</td>"
                f"<td colspan='5'>unreadable: {b['error']}</td></tr>"
            )
            continue
        pl = b.get("plugin") or {}
        name = pl.get("name") or pl.get("id") or b["bundle"]
        ver = pl.get("version") or ""
        prof = b.get("vm_profile") or ("plugin-only" if not b.get("has_vm") else "?")
        size = b.get("size_bytes") or 0
        size_mb = f"{size / 1_048_576:.1f} MB"
        ok = b.get("verified")
        badge = "ok" if ok else "FAILS VERIFY"
        cls = "" if ok else "bad"
        dl = f"/api/v1/catalog/{b['project']}/{b['bundle']}/download.zip"
        prov = b.get("provenance") or {}
        link = f"<a href={dl}>download .zip</a>" if ok else "&mdash;"
        origin = prov.get("origin_project") or ""
        when = prov.get("exported_at") or ""
        rows.append(
            f"<tr class='{cls}'>"
            f"<td>{name}</td><td>{ver}</td><td>{prof}</td><td>{size_mb}</td>"
            f"<td>{badge}</td><td>{link}</td>"
            f"<td class='prov'>{origin}<br>{when}</td>"
            f"</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='7'>no promoted artworks yet</td></tr>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>evmc catalog &mdash; done shelf</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;color:#111}}
 h1{{font-size:1.3rem}}
 table{{border-collapse:collapse;width:100%}}
 th,td{{text-align:left;padding:.45rem .6rem;border-bottom:1px solid #ddd;font-size:.9rem}}
 th{{color:#555;font-weight:600}}
 tr.bad td{{background:#fff4f4}}
 td.prov{{color:#888;font-size:.75rem}}
 a{{color:#0057d8}}
</style></head><body>
<h1>evmc catalog &mdash; finished artworks</h1>
<p>Each row is a promoted artwork on the shared drives. Download the self-contained
zip and run <code>evmctl import &lt;zip&gt;</code> on the exhibition host.</p>
<table>
<tr><th>artwork</th><th>version</th><th>vm</th><th>size</th><th>integrity</th><th></th><th>origin</th></tr>
{body}
</table>
</body></html>"""


def main() -> None:
    import uvicorn

    cfg = CatalogConfig()
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
    logger.info("evmc-catalog on %s:%s (drives=%s)", cfg.api_host, cfg.api_port, cfg.drives_root)
    uvicorn.run(create_app(cfg), host=cfg.api_host, port=cfg.api_port)


if __name__ == "__main__":
    main()
