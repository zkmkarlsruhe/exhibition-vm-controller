#!/usr/bin/env python3
"""Web UI for inspecting an exhibition VM's network traffic.

A standalone conservation tool (NOT part of the per-artwork controller, which
runs once per VM and often as a non-root user — live capture needs root). Pick
a VM, start a capture, and watch what it reaches for update live in the browser:
DNS lookups, TLS SNI, HTTP hosts, and a destination/protocol breakdown that
splits external (proxy/stub candidates) from host/controller heartbeat noise
and flags non-HTTP ports (RTMP/RTSP/…) an HTTP proxy would miss.

Shares all capture + analysis logic with capture_traffic.py.

Usage:
    sudo ./traffic_webui.py [--host 127.0.0.1] [--port 8090]
    # then open http://127.0.0.1:8090

Binds to localhost by default — do not expose a traffic-inspection UI on a
public interface. Tunnel over SSH if you need remote access.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import capture_traffic as ct


class CaptureSession:
    """Holds the single active capture (one at a time)."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.vm_name: str | None = None
        self.iface: str | None = None
        self.host_ip: str | None = None
        self.pcap_path: str | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, vm_name: str) -> None:
        if self.running:
            raise RuntimeError(f"already capturing '{self.vm_name}' — stop it first")
        taps, host_ip = ct.resolve_interfaces(vm_name)  # raises ValueError
        iface = taps[0]
        fd, pcap_path = tempfile.mkstemp(prefix="vmtraffic-", suffix=".pcap")
        os.close(fd)
        proc = subprocess.Popen(
            ["tcpdump", "-i", iface, "-nn", "-U", "-w", pcap_path],
            stderr=subprocess.DEVNULL,
        )
        self.proc, self.vm_name, self.iface = proc, vm_name, iface
        self.host_ip, self.pcap_path = host_ip, pcap_path

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def state(self) -> dict:
        size = (
            os.path.getsize(self.pcap_path)
            if self.pcap_path and os.path.exists(self.pcap_path)
            else 0
        )
        return {
            "running": self.running,
            "vm": self.vm_name,
            "iface": self.iface,
            "host_ip": self.host_ip,
            "pcap_bytes": size,
            "have_capture": bool(self.pcap_path and size > 24),  # >pcap global header
            "tshark": bool(__import__("shutil").which("tshark")),
        }


session = CaptureSession()
app = FastAPI(title="Exhibition VM Traffic Inspector")


def list_vms() -> list[dict]:
    """All libvirt VMs with running state."""
    running = set()
    r = subprocess.run(
        ["virsh", "list", "--state-running", "--name"], capture_output=True, text=True
    )
    for line in r.stdout.splitlines():
        if line.strip():
            running.add(line.strip())
    r = subprocess.run(["virsh", "list", "--all", "--name"], capture_output=True, text=True)
    vms = []
    for line in r.stdout.splitlines():
        name = line.strip()
        if name:
            vms.append({"name": name, "running": name in running})
    return vms


@app.get("/api/vms")
async def api_vms():
    return {"vms": list_vms()}


@app.get("/api/state")
async def api_state():
    return session.state()


@app.post("/api/start")
async def api_start(vm: str):
    if os.geteuid() != 0:
        raise HTTPException(403, "packet capture needs root — restart this tool with sudo")
    try:
        session.start(vm)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return session.state()


@app.post("/api/stop")
async def api_stop():
    session.stop()
    return session.state()


@app.get("/api/summary")
async def api_summary():
    if not session.pcap_path or not os.path.exists(session.pcap_path):
        return JSONResponse(
            {
                "tool": "none",
                "dns": [],
                "sni": [],
                "http": [],
                "external": [],
                "hostbound": [],
                "host_ip": None,
            }
        )
    return ct.analyze(session.pcap_path, session.host_ip)


@app.get("/api/download")
async def api_download():
    if not session.pcap_path or not os.path.exists(session.pcap_path):
        raise HTTPException(404, "no capture available")
    name = f"{(session.vm_name or 'vm').replace(' ', '_')}-traffic.pcap"
    return FileResponse(session.pcap_path, filename=name, media_type="application/vnd.tcpdump.pcap")


@app.get("/")
async def index():
    return HTMLResponse(PAGE)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><title>VM Traffic Inspector</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; background:#15171c; color:#dfe3ea; }
  header { padding: 14px 20px; background:#1d2027; border-bottom:1px solid #2c313c; position:sticky; top:0; }
  h1 { font-size: 16px; margin:0 0 10px; }
  .controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  select, button { font:inherit; padding:6px 12px; border-radius:6px; border:1px solid #3a4150;
                   background:#262b34; color:#dfe3ea; cursor:pointer; }
  button.primary { background:#2f6f4f; border-color:#2f6f4f; }
  button.stop { background:#7a3030; border-color:#7a3030; }
  button:disabled { opacity:.4; cursor:default; }
  #status { margin-left:auto; font-size:13px; color:#9aa3b2; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; background:#555; margin-right:6px; }
  .dot.live { background:#39d98a; box-shadow:0 0 6px #39d98a; }
  main { padding: 16px 20px; display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); }
  section { background:#1d2027; border:1px solid #2c313c; border-radius:8px; padding:12px 14px; }
  section h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:#8b94a4; margin:0 0 8px; }
  table { width:100%; border-collapse:collapse; }
  td { padding:3px 4px; vertical-align:top; border-bottom:1px solid #23272f; }
  td.n { text-align:right; color:#7f8895; width:48px; font-variant-numeric:tabular-nums; }
  .mono { font-family: ui-monospace, monospace; }
  .badge { font-size:11px; padding:1px 6px; border-radius:4px; margin-left:6px; background:#2c313c; color:#aab2c0; }
  .badge.warn { background:#5c2a2a; color:#ffb3b3; }
  .empty { color:#5d6573; font-style:italic; }
  .note { grid-column:1/-1; color:#9aa3b2; font-size:13px; }
  a { color:#6fb3ff; }
</style>
</head>
<body>
<header>
  <h1>Exhibition VM — Traffic Inspector</h1>
  <div class="controls">
    <select id="vm"></select>
    <button id="start" class="primary">▶ Start capture</button>
    <button id="stop" class="stop" disabled>■ Stop</button>
    <button id="dl" disabled>⤓ pcap</button>
    <span id="status"><span class="dot"></span>idle</span>
  </div>
</header>
<main id="main"></main>

<script>
let running = false, timer = null;

async function loadVMs() {
  const r = await fetch('/api/vms'); const {vms} = await r.json();
  const sel = document.getElementById('vm');
  sel.innerHTML = vms.map(v =>
    `<option value="${v.name}" ${v.running?'':'disabled'}>${v.name}${v.running?'':' (off)'}</option>`).join('');
}

function esc(s){ return (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function rows(items, mono) {
  if (!items.length) return '<tr><td class="empty">(none)</td></tr>';
  return items.map(it =>
    `<tr><td class="n">${it.count}</td><td class="${mono?'mono':''}">${esc(it.value)}</td></tr>`).join('');
}

function destRows(items) {
  if (!items.length) return '<tr><td class="empty">(none)</td></tr>';
  return items.map(d => {
    const warn = d.hint && d.hint.includes('NOT http');
    const badge = d.hint ? `<span class="badge ${warn?'warn':''}">${esc(d.hint)}</span>` : '';
    return `<tr><td class="n">${d.count}</td><td class="mono">${esc(d.ip)}:${esc(d.port)}/${d.proto}${badge}</td></tr>`;
  }).join('');
}

function sec(title, body){ return `<section><h2>${title}</h2><table>${body}</table></section>`; }

async function refresh() {
  const st = await (await fetch('/api/state')).json();
  const s = await (await fetch('/api/summary')).json();
  const m = document.getElementById('main');
  let html = '';
  if (s.tool === 'tcpdump')
    html += `<div class="note">tshark not installed — showing destinations + DNS only.
      Install <code>wireshark</code>/<code>tshark</code> for SNI &amp; HTTP Host extraction.</div>`;
  html += sec('DNS lookups', rows(s.dns, true));
  html += sec('TLS SNI (HTTPS targets)', rows(s.sni, true));
  html += sec('HTTP requests', rows(s.http, true));
  html += sec('External destinations — proxy/stub candidates', destRows(s.external));
  if (s.hostbound && s.hostbound.length)
    html += sec(`Host / controller (${esc(s.host_ip)}) — ignore`, destRows(s.hostbound));
  m.innerHTML = html;
}

function setRunning(st) {
  running = st.running;
  document.getElementById('start').disabled = running;
  document.getElementById('stop').disabled = !running;
  document.getElementById('vm').disabled = running;
  document.getElementById('dl').disabled = !st.have_capture;
  const dot = running ? 'dot live' : 'dot';
  const kb = (st.pcap_bytes/1024).toFixed(0);
  const label = running ? `capturing ${esc(st.vm||'')} on ${esc(st.iface||'')} — ${kb} KB`
                        : (st.have_capture ? `stopped — ${kb} KB captured` : 'idle');
  document.getElementById('status').innerHTML = `<span class="${dot}"></span>${label}`;
}

async function tick(){ const st = await (await fetch('/api/state')).json(); setRunning(st); await refresh(); }

document.getElementById('start').onclick = async () => {
  const vm = document.getElementById('vm').value;
  const r = await fetch('/api/start?vm='+encodeURIComponent(vm), {method:'POST'});
  if (!r.ok) { alert((await r.json()).detail); return; }
  setRunning(await r.json());
  if (!timer) timer = setInterval(tick, 2000);
};
document.getElementById('stop').onclick = async () => {
  setRunning(await (await fetch('/api/stop',{method:'POST'})).json());
  if (timer) { clearInterval(timer); timer = null; }
  await refresh();
};
document.getElementById('dl').onclick = () => location.href = '/api/download';

loadVMs().then(tick);
</script>
</body>
</html>"""


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8090, help="port (default: 8090)")
    args = p.parse_args()

    if os.geteuid() != 0:
        print(
            "warning: not running as root — live capture will fail until you "
            "restart with sudo (you can still view an existing capture).",
            file=sys.stderr,
        )

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
