# The exhibition handoff — from a revived VM to a running exhibition

Research (rvmc) revives an artwork as a VM; exhibition (evmctl) runs it unattended
on a dedicated machine. This is the seam between them (research/29 §8). It has two
ends, both built on one checksum-signed bundle format (`vmctl_core.promotion`), so
the two sides agree by construction and can't drift.

```
 rvmc: researcher promotes finished work
   └─ writes  <drive>/exhibition/<plugin-id>-<version>/   (THIN: overlay + base-ref)
                     │
                     │  shared drives/ filesystem — no network push, no trust boundary
                     ▼
 evmc-catalog (on vmctl.org): the "done" shelf
   ├─ scans every <drive>/exhibition/*, verifies checksums, lists
   └─ GET /download.zip  ── bakes on the way out ──▶  artwork.zip
                                                         │  (ONE self-contained
                                                         │   standalone image)
                                    scp / USB stick to the exhibition host
                                                         ▼
 evmctl-import (on the dedicated exhibition host):
   evmctl-import artwork.zip
   └─ verify → place disk → virsh define → ready snapshot → install plugin
      → write config → load conservation archive
```

## Why the download is *baked*

On the drive a bundle is **thin**: a copy-on-write overlay plus a reference to the
golden base it was authored on. That is right for research (goldens are shared and
reused). An exhibition host wants none of that — just the finished artwork image.

So the catalog **bakes** on download: it flattens the overlay onto its golden
(`qemu-img rebase -u` + `qemu-img convert`) into a single standalone `disk.qcow2`
with no backing chain, drops the golden and the overlay, and re-signs the bundle
as `profile: baked`. The exhibition machine needs no golden and no catalog — the
zip is everything.

## A promoted bundle is sealed

Once rvmc has written a bundle's checksums it seals the tree: files `0444`, directories `0555`.
The bundle is what an exhibition machine will import — possibly years later — and
`CHECKSUMS.sha256` is what tells that machine it is intact. Checksums detect damage; the seal
prevents the ordinary causes of it (a plugin writing beside its own files, a hand-edit "just to
fix one path", a re-promote that dies halfway), by making the write fail at the moment it happens
rather than surfacing as a mismatch long after the promotion stopped being reproducible.

Consequences worth knowing before they surprise you:

- **`rm -rf` on a promoted bundle fails**, even as its owner — a read-only directory does not let
  you unlink its entries. Deleting one on purpose is fine; go through
  `vmctl_core.promotion.remove_sealed_tree(path)` (which is what rvmc's project purge uses), or
  `chmod -R u+w` it first.
- Re-promoting the same id+version supersedes normally: the exporter unseals before it replaces.
- Baking and downloading are unaffected — the catalog only reads.
- The seal does **not** travel to the exhibition host. zip does not restore permission bits on
  extract, so the installed disk is writable, which it must be for the VM to boot.

It is a guardrail, not a security boundary: these are unix modes on files the promoting user
owns, so that user (and root) can take them off. Deliberately, which is the point.

## evmc-catalog (the shelf, on vmctl.org)

Runs alongside rvmc, rooted at the shared drives root. Read-only over the drives.

```
VMCTL_CATALOG_DRIVES_ROOT=/srv/vmctl/drives          # where rvmc promotes
VMCTL_CATALOG_CATALOG_PATH=/srv/vmctl/catalog/catalog.yaml  # to bake a thin bundle
VMCTL_CATALOG_API_PORT=8090
python -m vm_controller.catalog       # or: evmctl-catalog
```

- `GET /` — HTML shelf of finished artworks with per-item download links
- `GET /api/v1/catalog` — JSON list (`?deep=true` re-verifies + sizes)
- `GET /api/v1/catalog/{project}/{bundle}` — one bundle, verified
- `GET /api/v1/catalog/{project}/{bundle}/download.zip` — the baked zip

See `deployment/systemd/evmc-catalog.service`.

## evmctl-import (the exhibition host)

The importer defines the domain in `VMCTL_IMPORT_LIBVIRT_URI` (default `qemu:///system`). This
**must** be the libvirt the runtime controller connects to. Without an explicit `-c`, `virsh`
follows the caller's default — `qemu:///system` for root, but `qemu:///session` for any other
user — and the two halves never talk to each other: the import reports success into one libvirt
while the runtime looks for the domain in the other, and the exhibit simply never starts. (Seen
for real on the lab box, which is why every virsh the importer runs is now pinned.)

```
evmctl-import artwork.zip              # or: python -m vm_controller.importer
evmctl-import artwork.zip --dry-run    # show the plan, touch nothing
evmctl-import artwork.zip --force      # overwrite an existing install
```

Where things land (override with `VMCTL_IMPORT_*`):

| env | default | holds |
|---|---|---|
| `VMCTL_IMPORT_INSTALL_ROOT` | `/var/lib/evmc/artworks` | `<id>/disk.qcow2` + `domain.xml` |
| `VMCTL_IMPORT_CONFIG_DIR`   | `/etc/evmc/artworks`    | `<id>.yaml` runtime config |
| `VMCTL_IMPORT_PLUGINS_DIR`  | `/var/lib/evmc/plugins` | the artwork's plugin |
| `VMCTL_IMPORT_ARCHIVE_DIR`  | `/var/lib/evmc/archive` | the conservation archive |

Import **verifies the bundle** (every file re-hashed) and refuses a tampered bundle
or a non-baked libvirt bundle before touching libvirt. It then defines the VM, creates the
`ready` snapshot the runtime reverts to, installs the plugin and writes the
per-artwork config — after which the runtime controller
(`vm_controller.api --config /etc/evmc/artworks/<id>.yaml`) runs the artwork.

### Process bundles (emulators)

`evmctl-import artwork.zip` also accepts bundles with `vm.backend: process`.
It installs the contents of `vm/` at `VMCTL_IMPORT_INSTALL_ROOT/<id>/`, including
`process.json`, images and media, and creates a cold `ready` snapshot through
`ProcessBackend`. No `virsh` or `qemu-img` runs. Optional plugins and conservation
archives use the same destinations as libvirt imports.

The runtime config selects `backend: process`, points `process_dir` at that
instance, binds `api_host` to `127.0.0.1`, and sets `heartbeat_timeout` to 30 seconds.
Startup heartbeat grace defaults to 120 seconds; override it with
`--vm-startup-heartbeat-delay 180` or
`VMCTL_IMPORT_VM_STARTUP_HEARTBEAT_DELAY=180`. The host must provide the emulator
and display required by `process.json`.

An existing instance is refused unless `--force` is given. Forced imports stage
the replacement and its snapshot first, then stop the old process before replacing
its directory. The installed instance remains stopped until the runtime starts it.

### Multi-disk guests (macOS)

macOS is inherently multi-disk: OpenCore boots first and chainloads macOS off the
main disk. These bake as `profile: baked-multi` — the main overlay *and* each aux
(OpenCore) overlay are flattened into standalone disks (`vm/disk-0.qcow2`,
`vm/disk-1.qcow2`, … aux-first, matching the domain's disk order). The importer
places all of them, rewrites every `<source file>` positionally, and preserves the
OVMF `<loader>` + `isa-applesmc` `<qemu:commandline>`.

Because a baked image can't carry host firmware, a `baked-multi` bundle declares
`vm.host_requires` (e.g. `/usr/share/ovmf/OVMF.fd`); import surfaces any missing
prerequisite as a note. Install OVMF on the exhibition host before running a macOS
artwork.

Two remaining refusals (guards, not mis-wiring):
- **disk-count mismatch** — a domain whose `device='disk'` count differs from the
  disks the bundle ships is refused.
- **disk-over-`<qemu:arg file=...qcow2>`** — a disk passed through the qemu
  command line (not a `<disk>` element) can't be repointed and is refused.

`rvmc promote` emits `vm.aux_disks` + `vm.host_requires` for a macOS instance
(copying each OpenCore overlay into the bundle), so a promoted macOS project packs
as `baked-multi` automatically. A full macOS *boot* under the vmctl libvirt
template is separate, still-unproven work (Snow Leopard was proven under raw qemu).
