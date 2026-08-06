#!/usr/bin/env python3
"""Kiosk-hygiene panel — one-click, toggleable tweaks for clean media-art presentation.

A minimal stdlib web UI (no deps) over the recipe library. Pick the annoyances
to kill — firewall, screensaver, popups, breakout hardening, … — each Apply has
a Revert so you can re-enable it if an artwork needs the feature. Shows the live
guest screen and each tweak's current on/off state.

    sudo ./webui.py --vm "CYF-Example" --port 8090
    ./webui.py --agent 192.168.122.215:9009 --ca ./proxy/ca/mitmproxy-ca-cert.pem

Bind localhost; tunnel for remote. NO AUTH — trusted museum LAN only.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import recipes as R
import hosts as HOSTS

AGENT = None
VM = ""
CA_PEM = None


def resolve_ip(vm):
    r = subprocess.run(
        ["virsh", "domifaddr", vm, "--source", "agent"], capture_output=True, text=True, timeout=20
    )
    for line in r.stdout.splitlines():
        for tok in line.split():
            if tok.count(".") == 3 and not tok.startswith("127."):
                return tok.split("/")[0]
    return None


def grouped():
    groups = {}
    for rec in R.RECIPES:
        groups.setdefault(rec["group"], []).append(
            {
                "id": rec["id"],
                "label": rec["label"],
                "desc": rec["desc"],
                "admin": rec.get("admin", False),
                "kind": rec["kind"],
                "params": rec.get("params", {}),
            }
        )
    return groups


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj).encode())

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.replace("__VM__", VM).encode())
        elif p.path == "/api/recipes":
            self._json({"vm": VM, "groups": grouped()})
        elif p.path == "/api/states":
            st = {}
            for rec in R.RECIPES:
                try:
                    st[rec["id"]] = R.state(AGENT, rec)
                except Exception:
                    st[rec["id"]] = "unknown"
            self._json(st)
        elif p.path == "/api/hosts":
            try:
                self._json({"entries": HOSTS.parse(AGENT), "proxy": "192.168.122.1"})
            except Exception as e:
                self._json({"entries": [], "error": str(e)})
        elif p.path == "/api/screenshot.png":
            tmp = tempfile.mktemp(suffix=".ppm")
            subprocess.run(
                ["virsh", "screenshot", VM, tmp], capture_output=True, text=True, timeout=30
            )
            if os.path.exists(tmp):
                data = open(tmp, "rb").read()
                os.remove(tmp)
                self._send(200, "image/png", data)
            else:
                self._send(503, "text/plain", b"screenshot unavailable")
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/api/run":
            q = parse_qs(p.query)
            rid = q.get("id", [""])[0]
            op = q.get("op", ["apply"])[0]
            rec = R.RECIPE_BY_ID.get(rid)
            if not rec:
                return self._json({"error": "unknown recipe"}, 400)
            params = {k: v[0] for k, v in q.items() if k not in ("id", "op")}
            try:
                results = R.run(AGENT, rec, op, params=params, ca_pem=CA_PEM)
                new_state = R.state(AGENT, rec)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            self._json({"id": rid, "op": op, "results": results, "state": new_state})
        elif p.path in ("/api/hosts/add", "/api/hosts/remove"):
            q = parse_qs(p.query)
            host = q.get("host", [""])[0]
            try:
                if p.path.endswith("/add"):
                    ok, msg = HOSTS.add(AGENT, host, q.get("ip", ["192.168.122.1"])[0])
                else:
                    ok, msg = HOSTS.remove(AGENT, host)
                self._json({"ok": ok, "msg": msg, "entries": HOSTS.parse(AGENT)})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)}, 500)
        else:
            self._send(404, "text/plain", b"not found")


PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Kiosk Hygiene — __VM__</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{color-scheme:dark}
 body{font:14px/1.5 system-ui,sans-serif;margin:0;background:#15171c;color:#dfe3ea}
 header{padding:12px 18px;background:#1d2027;border-bottom:1px solid #2c313c;display:flex;gap:16px;align-items:center}
 h1{font-size:15px;margin:0}
 main{display:grid;grid-template-columns:380px 1fr;gap:16px;padding:16px 18px;align-items:start}
 .shot{position:sticky;top:16px}
 .shot img{width:100%;border:1px solid #2c313c;border-radius:6px;background:#000}
 .shot button,.row button{font:inherit;border:1px solid #3a4150;background:#262b34;color:#dfe3ea;border-radius:5px;padding:4px 10px;cursor:pointer}
 section{margin-bottom:14px}
 h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#8b94a4;margin:0 0 6px}
 .row{display:flex;align-items:center;gap:8px;padding:6px 8px;border-bottom:1px solid #23272f}
 .row .meta{flex:1;min-width:0}
 .row .lbl{font-weight:600}
 .row .desc{font-size:12px;color:#8b94a4}
 .pill{font-size:11px;padding:1px 7px;border-radius:9px;background:#2c313c;color:#aab2c0}
 .pill.on{background:#2f6f4f;color:#d6ffe9}
 .pill.off{background:#3a3f4a;color:#aab2c0}
 .badge{font-size:10px;color:#e0a03a;border:1px solid #5a4416;border-radius:4px;padding:0 4px;margin-left:6px}
 button.apply{background:#2f6f4f;border-color:#2f6f4f}
 button.revert{background:#5c3a2a;border-color:#5c3a2a}
 #toast{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);background:#222832;border:1px solid #3a4150;border-radius:6px;padding:8px 14px;max-width:70vw;display:none}
 .mono{font-family:ui-monospace,monospace;font-size:12px;color:#9aa3b2}
</style></head><body>
<header><h1>🎛 Kiosk Hygiene — <span class="mono">__VM__</span></h1>
  <span class="mono" style="color:#8b94a4">optional · repeatable · reversible</span></header>
<main>
 <div class="shot"><img id="screen" src="/api/screenshot.png"><br>
   <button onclick="refresh()">↻ refresh screen</button></div>
 <div><div id="hosts"></div><div id="panel"></div></div>
</main>
<div id="toast"></div>
<script>
let states={};
function toast(t){const e=document.getElementById('toast');e.textContent=t;e.style.display='block';clearTimeout(window._t);window._t=setTimeout(()=>e.style.display='none',5000)}
function refresh(){document.getElementById('screen').src='/api/screenshot.png?'+Date.now()}
function pill(s){return `<span class="pill ${s}">${s==='on'?'ON':s==='off'?'off':'?'}</span>`}
async function run(id,op){
  toast(op+' '+id+' …');
  const r=await fetch('/api/run?id='+id+'&op='+op,{method:'POST'});
  const d=await r.json();
  if(d.error){toast('error: '+d.error);return}
  states[id]=d.state; document.getElementById('st-'+id).outerHTML=`<span id="st-${id}">`+pill(d.state)+`</span>`;
  toast((d.results||[]).join(' | '));
  setTimeout(refresh,700);
}
async function load(){
  const {groups}=await (await fetch('/api/recipes')).json();
  let h='';
  for(const g in groups){
    h+=`<section><h2>${g}</h2>`;
    for(const r of groups[g]){
      const adm=r.admin?'<span class="badge">admin</span>':'';
      const act=r.kind==='action'
        ? `<button class="apply" onclick="run('${r.id}','apply')">Run</button>`
        : `<span id="st-${r.id}">${pill('?')}</span>
           <button class="apply" onclick="run('${r.id}','apply')">Apply</button>
           <button class="revert" onclick="run('${r.id}','revert')">Revert</button>`;
      h+=`<div class="row"><div class="meta"><div class="lbl">${r.label}${adm}</div>
          <div class="desc">${r.desc}</div></div>${act}</div>`;
    }
    h+='</section>';
  }
  document.getElementById('panel').innerHTML=h;
  states=await (await fetch('/api/states')).json();
  for(const id in states){const e=document.getElementById('st-'+id);if(e)e.outerHTML=`<span id="st-${id}">`+pill(states[id])+`</span>`}
}
async function loadHosts(){
  const d=await (await fetch('/api/hosts')).json();
  let rows=(d.entries||[]).map(e=>`<div class="row"><div class="meta mono">${e.ip} → ${e.host}</div>`+
    (e.ours?`<button class="revert" onclick="rmHost('${e.host}')">remove</button>`:`<span class="pill">system</span>`)+`</div>`).join('');
  document.getElementById('hosts').innerHTML=`<section><h2>Hosts redirects</h2>
    <div class="row"><input id="h-name" placeholder="artwork.local" style="flex:1;background:#262b34;color:#dfe3ea;border:1px solid #3a4150;border-radius:5px;padding:4px 8px">
      <button class="apply" onclick="addHost('${d.proxy||'192.168.122.1'}')">→ proxy</button>
      <button onclick="addHost('127.0.0.1')">→ localhost</button></div>
    ${rows||'<div class="row"><span class="desc">no redirects</span></div>'}</section>`;
}
async function addHost(ip){
  const h=document.getElementById('h-name').value.trim(); if(!h)return;
  const r=await (await fetch('/api/hosts/add?host='+encodeURIComponent(h)+'&ip='+ip,{method:'POST'})).json();
  toast(r.ok?('added '+h+' → '+ip):('failed: '+r.msg)); loadHosts(); setTimeout(refresh,700);
}
async function rmHost(h){
  const r=await (await fetch('/api/hosts/remove?host='+encodeURIComponent(h),{method:'POST'})).json();
  toast(r.ok?('removed '+h):('failed: '+r.msg)); loadHosts();
}
load(); loadHosts(); setInterval(refresh,8000);
</script></body></html>"""


def main():
    global AGENT, VM, CA_PEM
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--vm")
    ap.add_argument("--agent", help="agent ip:port (instead of --vm)")
    ap.add_argument("--ca", default="/cadir/mitmproxy-ca-cert.pem")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()

    VM = args.vm or args.agent or "?"
    if args.agent:
        ip, _, port = args.agent.partition(":")
        AGENT = R.Agent(ip, int(port or 9009))
        VM = args.vm or args.agent
    elif args.vm:
        ip = resolve_ip(args.vm)
        if not ip:
            sys.exit("could not resolve agent IP for %r" % args.vm)
        AGENT = R.Agent(ip)
    else:
        sys.exit("need --vm or --agent")
    try:
        CA_PEM = open(args.ca).read()
    except OSError:
        CA_PEM = None

    print(
        "kiosk-hygiene panel on http://%s:%d  (vm=%s, agent=%s:%d)"
        % (args.host, args.port, VM, AGENT.ip, AGENT.port),
        file=sys.stderr,
    )
    ThreadingHTTPServer((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    main()
