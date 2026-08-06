# Conservation toolkit

Tools for understanding and redirecting the network traffic of legacy artworks
running under the VM controller, so dead servers can be proxied or stubbed.

## Architecture — containerized, host stays clean (Option A)

All the software (mitmproxy, Python deps, the tools) runs in **one Docker
image** so nothing bleeds onto the host. The host keeps only one thing:

- **`host/virsh-broker.py`** — a tiny stdlib-only daemon on a unix socket that
  forwards a **whitelisted** set of `virsh` subcommands to the real `virsh`.
  The container talks to it through a drop-in `virsh` shim (`docker/virsh`), so
  every script (`host_mcp.py`, `capture_traffic.py`, …) runs **unchanged** —
  and the container never gets raw libvirt access.

```bash
# host (once): the only thing installed on the host
python3 host/virsh-broker.py                     # socket: /tmp/cons-sock/virsh.sock

# container
docker compose build
docker compose up -d proxy                        # mitmproxy archive server :8080
docker compose run --rm -T tools mcp "CYF-Example"      # MCP over stdio
docker compose run --rm --user 0 tools capture "CYF-Subfusion" -s 120
docker compose run --rm tools install-ca --vm "CYF-Example"
```

`network_mode: host` shares only the net namespace (a traffic tool needs it);
no packages/files land on the host. The container runs as the host user, so the
archive/CA it writes stay yours, not root's.

## `proxy/` — the conservation proxy layer (mitmproxy)

`serve_archive.py` is a mitmproxy addon that, for every request the artwork
makes: **serves it from the local archive** if present (dead server → revived);
else passes through to the live upstream and **records** the response into the
archive (auto-building it from whatever still answers); else returns a graceful
**stub**. Every request is logged so you see what the artwork reaches for.

```
archive/<host>/<path>           response body   (human-curatable)
archive/<host>/<path>.cons-meta  {status, content_type}
```

For HTTPS, push mitmproxy's CA into the guest's trust stores and bake it into
`ready`. `install-trust` covers all three: **Windows root** (silent registry
blob), **Java cacerts** (via the JRE's own `keytool`), and **NSS/Firefox**
(enables `security.enterprise_roots` so FF 49+ inherits the Windows root).
`install-ca` is the Windows-only shortcut.
### Getting traffic into the proxy

Three options, increasingly forceful:

1. **Explicit proxy** — point the guest's WinINet proxy at `host:8080` (the
   kiosk panel's *Route through proxy* does this). Works for proxy-aware apps
   (IE/WinINet); the demo used this.
2. **Hosts redirect** — for a *known* hardcoded hostname, the kiosk panel's
   **Hosts redirects** points it at the proxy in the guest's hosts file
   (`artwork.local → 192.168.122.1`). Surgical, no network plumbing.
3. **Transparent** — for raw-socket apps that ignore the proxy. The **broker**
   adds a per-VM iptables `REDIRECT` of `:80/:443` into mitmproxy (`--mode
   transparent`), auto-cleaned on broker exit; a stdlib **DNS responder**
   (`proxy/dns_responder.py`) answers fully-dead hostnames with the proxy IP.
   Needs the iptables NOPASSWD sudoers rule (see `deployment/sudoers.d/`).

   ```bash
   docker compose run --rm tools transparent-on "CYF-Subfusion"   # broker adds the REDIRECT
   docker compose run -d  --rm -e CONS_MODE=transparent tools proxy
   docker compose run -d  --rm tools dns --to 192.168.122.1 --host '*.sounddogs.com'
   # ... and transparent-off when done (broker also auto-cleans on exit)
   ```

   Transparent changes only *how bytes reach the proxy* — HTTPS still needs the
   CA trusted (`install-trust`), and it can't beat cert pinning.

### Serving the archive without any host privilege (the default)

For dead hosts (hostname-based — the majority), you don't need iptables/root at
all. Point the host at the gateway (hosts redirect or DNS responder), and let
the **existing nginx on :80** reverse-proxy the request to a small unprivileged
**archive server** that returns the archived response by `Host` + path:

```
guest ── deadhost → 192.168.122.1 ──▶ nginx :80 (already running)
                                  ──▶ archive_server :8081 (unprivileged) ──▶ archive
```

```bash
docker compose run -d --rm tools archive --listen 127.0.0.1 --port 8081
# + deployment/nginx/conservation-archive.conf (default_server → :8081)
```

nginx already owns :80, so **no iptables, no sudoers, no root, no privileged
bind** by our own process. `proxy/archive_store.py` is shared with the mitmproxy
addon, so the on-disk archive never drifts between record (mitmproxy) and serve
(nginx) paths. Only hardcoded-IP artworks still need the iptables toggle.

### Front door in Docker + operator access (two planes)

You can run the whole `:80` front door in Docker too — **no host nginx**. The
`nginx` + `archive` compose services run **non-root**; the Docker daemon
publishes nginx on **`192.168.122.1:80` only** (the VM network), so nothing is
host-wide and nothing binds a privileged port as root:

```
guest ─:80─▶ nginx (Host-routed):  gateway → host controller
                                    dead host → archive container
```
```bash
docker compose up -d archive nginx       # front door, scoped to the VM-net IP
```

- **Guest plane** — `192.168.122.1:80` (above). VM-network only; bind every
  conservation port to `192.168.122.1` so nothing is host-wide.
- **Operator plane** — the controller `/ui` and the conservation UIs bind
  **localhost** (kiosk/traffic default to `--host 127.0.0.1`). Reach them over an
  **SSH tunnel**, never on the VM net — so visitors' machines can't touch the
  management UIs:
  ```bash
  ssh -L 8002:127.0.0.1:8002 garden-pve    # controller UI  → http://localhost:8002/ui
  ssh -L 8090:127.0.0.1:8090 garden-pve    # kiosk-hygiene UI
  ```

Migrating off the host nginx: stop it, bind the controller to a Docker-reachable
address (`api_host: 0.0.0.0`, covered by the VM-origin CSRF guard), point guests
at the new nginx. To keep the real guest **source IP** (for per-VM routing),
attach nginx to a **macvlan on virbr0** instead of `-p`. Validated:
nginx-unprivileged on the VM IP routes Host=deadhost → archive (200) and
Host=gateway → controller.

**Validated:** broker whitelist + `virsh` shim; `host_mcp` from the container
listing 44 tools (15 host via broker + 29 guest via bridge); proxy record →
serve-from-archive (`X-Conserved: archive`) → stub (`X-Conserved: stub`).

## `capture_traffic.py` — Phase 0: see what the artwork reaches for

Before you proxy or stub anything, get the complete inventory of what an
artwork actually touches on the network.

The tool captures with **tcpdump on the VM's own tap interface** (`vnetN`). That
tap sits below the guest, so it is *ground truth*: every packet the guest emits,
all ports, TCP and UDP, encrypted or not — and only that one VM (no cross-talk
from the other artworks on the bridge). This is the only layer that is truly
exhaustive; a MITM proxy only ever sees the HTTP(S) you route into it.

```bash
sudo ./capture_traffic.py CYF-Subfusion --seconds 120
sudo ./capture_traffic.py "CYF-Example" --out /tmp/capture.pcap   # Ctrl-C to stop
```

It writes a `.pcap` (open in Wireshark for full detail) and prints a summary:

- **DNS lookups** — names it tried to resolve
- **TLS SNI** — HTTPS targets, readable *without* decrypting (needs `tshark`)
- **HTTP Host + path** — plain-HTTP targets (needs `tshark`)
- **Destinations** — IP:port with a protocol breakdown, splitting *external*
  (proxy/stub candidates) from *host/controller* traffic (heartbeat/API noise).
  Non-HTTP ports like **RTMP 1935 / RTSP 554** are flagged explicitly — those
  will **not** be caught by an HTTP proxy and need separate handling.

Requires `tcpdump`. `tshark` (wireshark-common) is optional but adds the SNI /
HTTP-Host / DNS-name extraction; without it you still get destinations + DNS
names, plus the raw pcap for Wireshark.

## `traffic_webui.py` — browser view of the same capture

A standalone web UI over the capture/analysis logic. Pick a VM, start a
capture, and watch the summary update live in the browser (DNS, SNI, HTTP
hosts, and the external-vs-host destination breakdown with non-HTTP ports
flagged in red). Download the raw `.pcap` for Wireshark when done.

```bash
sudo ./traffic_webui.py            # then open http://127.0.0.1:8090
sudo ./traffic_webui.py --port 8097
```

It is **standalone on purpose** — not folded into the per-artwork controller,
which runs once per VM and often as a non-root user, while live capture needs
root. Binds to `127.0.0.1` by default; tunnel over SSH for remote access,
don't expose a traffic-inspection UI publicly. Shares all logic with
`capture_traffic.py` (`analyze()` returns the same structured summary the CLI
prints), so the two never drift.

### Why two tools, not one

`tcpdump` answers *"what does it reach for"* (complete). A MITM proxy answers
*"what's in the requests, and can I replay them"* (HTTP/S only, ports you
redirect). Run capture first, then point the proxy at the HTTP(S) you found and
handle any non-HTTP ports the capture revealed.

## `host_mcp.py` — one MCP endpoint for the whole debug surface

The unified server an AI agent connects to in order to work on an artwork
**inside and outside** the VM at once:

```bash
claude mcp add legacy-host -- python3 host_mcp.py --vm "CYF-Example"
```

It publishes **43 tools** over MCP (stdio):

- **15 host / hypervisor tools** (`vm_*`) via libvirt+QEMU — work even on a dead
  or agentless guest: `vm_state`/`start`/`reboot`/`reset`/`shutdown`,
  `vm_snapshot_list`/`create`/`revert`/`delete`, `vm_screenshot` (framebuffer,
  any OS), `vm_send_key` (inject keystrokes), `vm_dumpxml`, `vm_net_info`, and
  `vm_virsh` (raw escape hatch).
- **28 guest tools** (`guest_*`) — every tool the in-guest agent exposes, fetched
  live and forwarded over the TCP bridge (`guest_window_controls`,
  `guest_process_modules`, `guest_http_get`, …). See `guest_agent/`.

So the agent can revert a snapshot, send a key to a frozen dialog, watch the
framebuffer, AND walk the guest's processes/windows/registry — one connection.
The guest agent's IP is auto-resolved via libvirt (`--agent ip:port` to
override); if the guest agent is down, the `vm_*` tools still work.

## `kiosk/` — presentation-hygiene panel

A minimal web UI of **toggleable** one-click tweaks to make a guest clean for
unattended media-art presentation — kill the classic museum-XP annoyances, and
optionally harden against visitor breakout. **Every Apply has a Revert** (re-
enable anytime if an artwork needs the feature) and shows each tweak's current
on/off state, plus a live screenshot.

```bash
docker compose run --rm tools kiosk --vm "CYF-Example" --host 0.0.0.0 --port 8090
```

21 tweaks across **Network** (firewall off OS-aware · proxy · CA), **Display**
(no screensaver · never-sleep · focus · hide icons), **Annoyances** (balloon
tips · low-disk · autoplay · sounds · error dialogs · auto-updates), **Breakout**
(disable TaskMgr/RegEdit/cmd/Run/Control-Panel/right-click · trim Ctrl-Alt-Del),
and **Actions** (dismiss stray dialogs).

Registry tweaks go through the agent's **native** `reg_set`/`reg_delete`, so
they survive *and can undo* breakout tweaks that disable `regedit`/`reg.exe`/
`cmd` (which would break `run_shell`-based tweaks). Firewall/power/service
recipes are **OS-aware** (XP vs Win7+). HKLM tweaks are flagged "admin" and
report failure honestly rather than pretending. The recipe library
(`kiosk/recipes.py`) is plain data — easy to curate.

## Roadmap (not yet built)

- expose transparent-mode on/off as a one-click toggle in the kiosk UI (CLI today).
- optional controller plugin surfacing live flows to `/api/v1/state` + SSE.
- NSS support for **pre-49 Mozilla** (needs the NSS `certutil` against cert8.db;
  `install-trust` handles FF 49+ via enterprise-roots today).

## Done

- `capture_traffic.py` / `traffic_webui.py` — traffic inventory.
- `guest_agent/` — in-guest MCP agent (29 tools incl. silent `install_cert`).
- `host_mcp.py` — host+guest unified MCP (44 tools).
- `proxy/serve_archive.py` + `install_proxy_ca.py` — archive-serving proxy + CA push.
- containerized (Option A) with the `virsh-broker`.
