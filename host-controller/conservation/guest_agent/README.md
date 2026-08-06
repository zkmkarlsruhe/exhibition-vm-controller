# Legacy in-guest MCP agent

A hand-rolled MCP server for legacy guests that **Ghost Commander can't reach**
(GC needs modern Windows). It speaks the MCP wire protocol directly — JSON-RPC
2.0 over a newline-delimited stream — and gives a remote AI agent a full debug
surface inside a black-box artwork. **28 tools**, dependency-free:

| Group | Tools |
|---|---|
| System / shell | `system_info`, `env`, `run_shell` |
| Processes | `list_processes`, `process_modules` (loaded DLLs), `kill_process` |
| Windows / UI | `list_windows`, `find_window`, `window_info`, `window_controls` (no-UIA control tree) |
| Drive the app | `click`, `move_mouse`, `send_keys`, `send_key`, `show_window`, `pixel` |
| Screen | `screenshot` |
| Files | `list_dir`, `read_file` (+offset), `write_file`, `delete_file`, `move_file`, `make_dir`, `find_files`, `file_hash` |
| Network (guest's stack) | `http_get`, `dns_lookup`, `tcp_probe` |
| Trust / conservation | `install_cert` — add a CA to the Windows root store **silently** (direct registry blob write; no `certutil`, no consent prompt) so the artwork trusts a MITM/proxy CA |

`run_shell` is the escape hatch for everything else (`reg query`, `netstat -ano`,
`sc query`, `systeminfo`, …), so those aren't wrapped separately.

## Two implementations

| | `go/legacy_agent.go` ⭐ | `legacy_agent.py` |
|---|---|---|
| Form | **single static `.exe`, drop-in, no runtime** | `.py`, needs Python 3.4 in the guest |
| Build | cross-compile from Linux (`build.sh`) | none |
| Runs on | Windows XP → Windows 10 (`386`) | Windows XP (Python 3.4) |
| Role | **what you deploy** | readable reference / when you can't rebuild the exe |

The Go build is the one to ship — one ~2.5 MB executable you drop into the
guest, nothing to install. The Python version is the same tool kept legible
for understanding/porting.

### Build the exe (from Linux)

```bash
# Go 1.10.x is REQUIRED (see below). Then:
GOROOT=/tmp/go110 PATH=/tmp/go110/bin:$PATH ./go/build.sh   # -> go/legacy-agent.exe
```

`GOOS=windows GOARCH=386` produces a binary that runs Windows XP → Windows 10 —
**but only when built with the Go 1.10.x toolchain** (the last with XP support).

> ⚠️ **Modern Go does NOT work for XP** (verified on a real XP guest). Go ≥1.11
> stamps a Windows-7 minimum into the PE header (`MinSubsystemVersion 6.1`), and
> XP's loader rejects the binary with *"Exec format error"*. Go 1.10 stamps
> `4.0`, which XP accepts. `build.sh` checks this and **fails** if the version is
> wrong, so a bad exe can't slip through. Get Go 1.10.8:
> `curl -LO https://go.dev/dl/go1.10.8.linux-amd64.tar.gz`.

## Why TCP + a bridge instead of stdio

MCP's stdio transport is just newline-delimited JSON over a pipe, but the
client (Claude Code on the host) can't launch a process *inside* the VM. So the
agent listens on TCP with the identical framing, and you bridge stdio↔TCP:

```bash
# preferred — socat handles half-close cleanly
claude mcp add legacy-xp -- socat - TCP:192.168.122.50:9009
# or netcat
claude mcp add legacy-xp -- nc 192.168.122.50 9009
```

No HTTP, no SSE, no session management — the hard transport problem disappears.

## Deploy into a Windows XP guest

The exe is built `-H windowsgui`, so it runs **hidden in the background — no
console window**. It's a TCP server; once autostarted it just sits there.

1. Drop **`legacy-agent.exe`** into the guest (the controller's
   `guest_upload.py` can push it over the QEMU agent, or use a share). No Python,
   no runtime.
2. **Autostart it at login** so it lands in the **interactive desktop session**
   — that's both how it runs unattended *and* what makes `screenshot` /
   `list_windows` see the real desktop (a session-0 service can't). Either:
   ```bat
   reg add "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" ^
       /v LegacyAgent /d "C:\legacy-agent.exe" /f
   ```
   or a `shell:startup` shortcut to `C:\legacy-agent.exe`. Don't install it as a
   Windows service (wrong window station).
3. **Open the firewall** for the agent — XP SP2+ blocks inbound by default, so
   the bridge can't reach port 9009 without this:
   ```bat
   netsh firewall add allowedprogram C:\legacy-agent.exe LegacyAgent ENABLE
   netsh firewall add portopening TCP 9009 LegacyAgent9009
   ```
4. **Bake it into the `ready` snapshot** — otherwise the next revert wipes it.
5. From the host: `claude mcp add legacy-xp -- socat - TCP:<vm-ip>:9009`.

Because it's a GUI-subsystem (windowless) build, `--stdio` has no console to
attach to when double-clicked; use the TCP transport in production. For local
stdio testing, run the Linux build or `legacy_agent.py --stdio`.

> **No auth** — trusted museum LAN only, same posture as Ghost Commander. Don't
> expose port 9009 off the artwork network.

## Validated

Tested on a **real Windows XP guest** (5.1.2600), driven over the QEMU guest
agent (`--stdio` fed via `guest-exec` input-data):

- ✅ **Go 1.10.8 build runs on XP.** `initialize`, `system_info` (reports
  `winxp` / `386` / `go1.10.8`), and `run_shell` → real XP `tasklist` (31
  processes, exit 0) all work.
- ❌ **Modern Go (1.22) build is rejected by XP** — *Exec format error*, traced
  to PE `MinSubsystemVersion 6.1`. This is why `build.sh` enforces ≤ 5.1.
- ✅ **`list_windows` + `screenshot` work end-to-end in real deployment.**
  Autostarted via the `Run` key (hidden, windowsgui) in the interactive session
  and reached over the TCP bridge: `list_windows` returns the desktop's
  `Program Manager` window, and the agent's own GDI `screenshot` returns a full
  **1024×768 BMP over TCP** (pixel-identical to `virsh screenshot`).
  - Note the two pitfalls this avoids: via `qemu-ga` `guest-exec` instead, the
    exe runs in the non-interactive **service window station** (`list_windows` →
    0) and a multi-MB BMP overflows `guest-exec` output capture. So: interactive
    autostart + TCP, not service-context exec.
- Host-side `virsh screenshot` also grabs the framebuffer with zero guest help
  (the universal "see" channel for guests with no agent at all).
- **Python** version: 3.4-compat linted; identical handshake over stdio + TCP.

Off-Windows, the Win32 tools degrade gracefully (`go vet` clean for linux and
`windows/386`).

## Windows versions (XP → Windows 10)

The agent and the whole pipeline are **fit for Win7/8/10**, not just XP — one
`windows/386` binary spans the range. Win7 sits in the gap **above XP but below
Ghost Commander** (GC needs modern Electron = Win10), so this agent is the right
tool there too. Per-version differences:

| | Windows XP | Windows 7 / 8 | Windows 10 |
|---|---|---|---|
| agent exe (Go 1.10) | ✅ | ✅ | ✅ (or modern Go) |
| Win32 tools / `install_cert` | ✅ | ✅ | ✅ |
| **firewall rule** | `netsh firewall add ...` | **`netsh advfirewall firewall add rule ...`** | same as Win7 |
| **autostart** | Run key (interactive) | Run key (interactive) — **never a service**: Session-0 isolation (Vista+) blinds a service to the desktop, so `screenshot`/`list_windows` need the login session | same |
| machine-scope cert (HKLM) | ok | needs **UAC elevation** — default `user` scope (HKCU) avoids it | same |

So the only real changes for Win7+ are the **firewall command** (`advfirewall`)
and being strict about **interactive-session autostart** (which we already are).
Default user-scope cert install side-steps UAC. Toolchain: Go 1.10 works
everywhere; for a Win7-only guest you *may* build with modern Go ≤ 1.20 (1.21
dropped Win7) for newer TLS in `http_get`.

## Other legacy systems

The MCP protocol layer here is ~80 portable lines; only the **runtime** and the
**JSON library** change per target. Two tiers:

### Tier 1 — an in-guest agent is possible
Any OS with a socket-capable scripting runtime. Port = swap runtime + JSON dep.

| Guest | How | Notes |
|---|---|---|
| Windows 10/8/7 | **Ghost Commander** | rich UIA introspection; not this |
| **Windows XP → 10** | **`legacy-agent.exe` (Go)** | one binary covers the whole range |
| Windows 98/ME | Python 2.5 + `simplejson`, or AutoIt | Go needs XP; py2 port (`json` is 2.6+) |
| Mac OS X PPC (10.2–10.5) | bundled Python 2.3–2.5 | Go has no PPC-darwin; py2 port |
| old Linux/Unix | system Python or `sh`+`nc` | trivial |

### Tier 2 — no practical in-guest runtime (Mac OS 9, DOS, exotic)
Fall back to **hypervisor-level** tools that need zero guest cooperation — and
expose them through the *same* MCP surface (host shim), so the agent can still
**see and act** even with no agent inside:

- **`virsh screenshot`** — framebuffer grab, any guest OS (the "see" channel).
- **`virsh send-key` / QEMU monitor `sendkey` + mouse** — input injection (the
  "act" channel).
- **traffic capture** — `capture_traffic.py`, bridge-level, no guest help.

What Tier 2 loses vs Tier 1: filesystem/process/registry introspection. What it
keeps: watch the screen, drive the keyboard/mouse, and see the network — enough
to research and operate even a Mac OS 9 artwork.
