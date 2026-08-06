#!/usr/bin/env python3
"""Kiosk-hygiene recipe library — toggleable tweaks for clean media-art presentation.

Each recipe kills a presentation annoyance (or hardens against kiosk breakout)
and is fully **reversible** ("better safe than sorry" — re-enable any time) and
**repeatable** (idempotent). Registry recipes go through the agent's NATIVE
reg_set/reg_delete (advapi32), so they survive — and can undo — breakout tweaks
that disable regedit/reg.exe/cmd. Firewall/power/service recipes are OS-aware.

Grounded in verified keys (see README sources). Used by kiosk/webui.py.
"""

import json
import socket
import subprocess

DELETE = "__DELETE__"  # revert by deleting the value

SYS = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
EXP = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
DESKTOP = r"Control Panel\Desktop"
INET = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# reg(value) helper: one registry toggle entry
def reg(hive, key, value, vtype, on, off, os=None):
    return {"hive": hive, "key": key, "value": value, "type": vtype, "on": on, "off": off, "os": os}


RECIPES = [
    # ---- Network ----
    dict(
        id="install_ca",
        label="Install proxy CA",
        group="Network",
        admin=False,
        kind="special",
        desc="Trust the conservation proxy's CA so HTTPS can be intercepted (silent, all stores).",
    ),
    dict(
        id="set_proxy",
        label="Route through proxy",
        group="Network",
        admin=False,
        kind="reg",
        desc="Point WinINet at the conservation proxy (param: proxy host:port).",
        reg=[
            reg("HKCU", INET, "ProxyEnable", "dword", 1, 0),
            reg("HKCU", INET, "ProxyServer", "sz", "{proxy}", DELETE),
        ],
        params={"proxy": "192.168.122.1:8080"},
    ),
    dict(
        id="firewall_off",
        label="Disable firewall",
        group="Network",
        admin=True,
        kind="cmd",
        desc="Turn off Windows Firewall completely (OS-aware).",
        apply={
            "xp": "netsh firewall set opmode disable",
            "modern": "netsh advfirewall set allprofiles state off",
        },
        revert={
            "xp": "netsh firewall set opmode enable",
            "modern": "netsh advfirewall set allprofiles state on",
        },
    ),
    dict(
        id="transparent",
        label="Transparent intercept",
        group="Network",
        admin=True,
        kind="special",
        desc="Redirect this VM's :80/:443 into the proxy via the broker (iptables; needs the sudoers rule). For raw-socket apps that ignore the proxy. Prefer hosts-redirect for known hostnames.",
        params={"port": "8080"},
    ),
    # ---- Display ----
    dict(
        id="screensaver",
        label="No screensaver",
        group="Display",
        admin=False,
        kind="reg",
        desc="Stop the screensaver from kicking in.",
        reg=[reg("HKCU", DESKTOP, "ScreenSaveActive", "sz", "0", "1")],
    ),
    dict(
        id="never_sleep",
        label="Never sleep display",
        group="Display",
        admin=True,
        kind="cmd",
        desc="Stop the monitor/PC from sleeping (Win7+ powercfg; XP via Power Options).",
        apply={
            "modern": "powercfg -change -monitor-timeout-ac 0 -standby-timeout-ac 0 -disk-timeout-ac 0",
            "xp": "powercfg /change Home/Office monitor-timeout-ac 0",
        },
        revert={
            "modern": "powercfg -change -monitor-timeout-ac 15 -standby-timeout-ac 30",
            "xp": "echo set via Power Options on XP",
        },
    ),
    dict(
        id="foreground_lock",
        label="Allow focus steal",
        group="Display",
        admin=False,
        kind="reg",
        desc="Let the artwork window grab foreground focus immediately.",
        reg=[reg("HKCU", DESKTOP, "ForegroundLockTimeout", "dword", 0, 200000)],
    ),
    dict(
        id="hide_icons",
        label="Hide desktop icons",
        group="Display",
        admin=False,
        kind="reg",
        desc="Hide all desktop icons for a clean presentation.",
        reg=[reg("HKCU", ADV, "HideIcons", "dword", 1, 0)],
    ),
    # ---- Annoyances ----
    dict(
        id="balloon_tips",
        label="No balloon tips",
        group="Annoyances",
        admin=False,
        kind="reg",
        desc="Disable taskbar balloon notifications.",
        reg=[reg("HKCU", ADV, "EnableBalloonTips", "dword", 0, 1)],
    ),
    dict(
        id="low_disk",
        label="No low-disk warning",
        group="Annoyances",
        admin=False,
        kind="reg",
        desc="Suppress the low-disk-space warning.",
        reg=[reg("HKCU", EXP, "NoLowDiskSpaceChecks", "dword", 1, DELETE)],
    ),
    dict(
        id="autoplay",
        label="No autoplay",
        group="Annoyances",
        admin=True,
        kind="reg",
        desc="Disable AutoRun/AutoPlay on all drives (HKCU + HKLM; XP needs both).",
        reg=[
            reg("HKCU", EXP, "NoDriveTypeAutoRun", "dword", 255, DELETE),
            reg("HKLM", EXP, "NoDriveTypeAutoRun", "dword", 255, DELETE),
        ],
    ),
    dict(
        id="mute_sounds",
        label="Mute system sounds",
        group="Annoyances",
        admin=False,
        kind="reg",
        desc="Set the sound scheme to None (no error dings).",
        reg=[reg("HKCU", r"AppEvents\Schemes", "", "sz", ".None", ".Default")],
    ),
    dict(
        id="error_reporting",
        label="No error dialogs",
        group="Annoyances",
        admin=True,
        kind="reg",
        desc="Disable Windows Error Reporting popups (OS-aware).",
        reg=[
            reg(
                "HKLM",
                r"SOFTWARE\Microsoft\PCHealth\ErrorReporting",
                "DoReport",
                "dword",
                0,
                1,
                os="xp",
            ),
            reg(
                "HKLM",
                r"SOFTWARE\Microsoft\PCHealth\ErrorReporting",
                "ShowUI",
                "dword",
                0,
                1,
                os="xp",
            ),
            reg(
                "HKLM",
                r"SOFTWARE\Microsoft\Windows\Windows Error Reporting",
                "Disabled",
                "dword",
                1,
                0,
                os="modern",
            ),
        ],
    ),
    dict(
        id="auto_updates",
        label="Disable auto-updates",
        group="Annoyances",
        admin=True,
        kind="cmd",
        desc="Stop Automatic Updates (no reboot prompts).",
        apply={"all": "sc config wuauserv start= disabled & net stop wuauserv"},
        revert={"all": "sc config wuauserv start= auto & net start wuauserv"},
    ),
    # ---- Breakout hardening (HKCU policies; reversible via native API even after disabling cmd/regedit) ----
    dict(
        id="disable_taskmgr",
        label="Disable Task Manager",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Block Task Manager so visitors can't kill the artwork.",
        reg=[reg("HKCU", SYS, "DisableTaskMgr", "dword", 1, DELETE)],
    ),
    dict(
        id="disable_regedit",
        label="Disable RegEdit",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Block regedit (the agent's native API still works to revert this).",
        reg=[reg("HKCU", SYS, "DisableRegistryTools", "dword", 1, DELETE)],
    ),
    dict(
        id="disable_cmd",
        label="Disable cmd.exe",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Block the command prompt. NOTE: while on, run_shell recipes won't work — reg/breakout recipes still do.",
        reg=[reg("HKCU", SYS, "DisableCMD", "dword", 2, DELETE)],
    ),
    dict(
        id="no_run",
        label="Remove Run box",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Hide the Start > Run command.",
        reg=[reg("HKCU", EXP, "NoRun", "dword", 1, DELETE)],
    ),
    dict(
        id="no_control_panel",
        label="Block Control Panel",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Block Control Panel access.",
        reg=[reg("HKCU", EXP, "NoControlPanel", "dword", 1, DELETE)],
    ),
    dict(
        id="no_context_menu",
        label="No desktop right-click",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Disable the desktop/Explorer right-click menu.",
        reg=[reg("HKCU", EXP, "NoViewContextMenu", "dword", 1, DELETE)],
    ),
    dict(
        id="trim_cad",
        label="Trim Ctrl+Alt+Del",
        group="Breakout",
        admin=False,
        kind="reg",
        desc="Remove Lock/Change-Password from the Ctrl+Alt+Del screen.",
        reg=[
            reg("HKCU", SYS, "DisableLockWorkstation", "dword", 1, DELETE),
            reg("HKCU", SYS, "DisableChangePassword", "dword", 1, DELETE),
        ],
    ),
    # ---- Actions (one-shot) ----
    dict(
        id="close_popups",
        label="Dismiss stray dialogs",
        group="Actions",
        admin=False,
        kind="action",
        desc="Close any open dialog windows (#32770) cluttering the screen.",
    ),
]

RECIPE_BY_ID = {r["id"]: r for r in RECIPES}


# ---------------------------------------------------------------------------
# agent client + runner
# ---------------------------------------------------------------------------


class Agent:
    def __init__(self, ip, port=9009):
        self.ip, self.port = ip, port
        self._family = None

    def call(self, name, args, tmo=30):
        s = socket.create_connection((self.ip, self.port), timeout=tmo)
        f = s.makefile("rwb")
        f.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n").encode())
        f.flush()
        f.readline()
        f.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args},
                    }
                )
                + "\n"
            ).encode()
        )
        f.flush()
        r = json.loads(f.readline())
        s.close()
        res = r["result"]
        txt = res["content"][0]["text"]
        try:
            return json.loads(txt), res.get("isError", False)
        except (ValueError, TypeError):
            return txt, res.get("isError", False)

    def family(self):
        if self._family is None:
            out, _ = self.call("run_shell", {"command": "ver"})
            ver = out.get("stdout", "") if isinstance(out, dict) else ""
            self._family = "xp" if "5.1" in ver or "5.2" in ver else "modern"
        return self._family


def _entries(recipe, fam):
    return [e for e in recipe.get("reg", []) if e["os"] in (None, fam)]


def _fmt(v, params):
    return v.format(**params) if isinstance(v, str) and "{" in v else v


def state(ag, recipe):
    """Return 'on' (hardened), 'off' (default), or 'unknown'."""
    if recipe["kind"] != "reg":
        return "unknown"
    e = _entries(recipe, ag.family())
    if not e:
        return "unknown"
    e = e[0]
    got, _ = ag.call("reg_get", {"hive": e["hive"], "key": e["key"], "value": e["value"]})
    if not isinstance(got, dict) or not got.get("exists"):
        return "off"
    return "on" if str(got.get("data")) == str(_fmt(e["on"], recipe.get("params", {}))) else "off"


def run(ag, recipe, op, params=None, ca_pem=None):
    """op = 'apply' or 'revert'. Returns a list of result strings."""
    params = {**recipe.get("params", {}), **(params or {})}
    fam = ag.family()
    out = []

    if recipe["id"] == "transparent":
        sub = "transparent-on" if op == "apply" else "transparent-off"
        r = subprocess.run(
            ["virsh", sub, ag.ip, str(params.get("port", "8080"))],
            capture_output=True,
            text=True,
            timeout=20,
        )
        out.append("%s: %s" % (sub, (r.stdout.strip() or r.stderr.strip() or "ok")))
        return out

    if recipe["id"] == "install_ca":
        if op == "apply":
            res, err = ag.call("install_cert", {"pem": ca_pem, "scope": "user"})
            out.append(
                "install_cert: "
                + (
                    "OK %s" % res.get("thumbprint")
                    if isinstance(res, dict) and res.get("installed")
                    else "FAILED"
                )
            )
        else:
            out.append("revert: remove the CA from the store manually (per-thumbprint)")
        return out

    if recipe["kind"] == "reg":
        for e in _entries(recipe, fam):
            if op == "apply":
                r, err = ag.call(
                    "reg_set",
                    {
                        "hive": e["hive"],
                        "key": e["key"],
                        "value": e["value"],
                        "type": e["type"],
                        "data": _fmt(e["on"], params),
                    },
                )
            elif e["off"] == DELETE:
                r, err = ag.call(
                    "reg_delete", {"hive": e["hive"], "key": e["key"], "value": e["value"]}
                )
            else:
                r, err = ag.call(
                    "reg_set",
                    {
                        "hive": e["hive"],
                        "key": e["key"],
                        "value": e["value"],
                        "type": e["type"],
                        "data": _fmt(e["off"], params),
                    },
                )
            out.append(
                "%s\\%s = %s"
                % (e["hive"], e["value"] or "(default)", "FAILED: %s" % r if err else "ok")
            )
        return out

    if recipe["kind"] == "cmd":
        cmds = recipe[op]
        cmd = cmds.get(fam) or cmds.get("all")
        if not cmd:
            return ["(no command for %s)" % fam]
        r, err = ag.call("run_shell", {"command": cmd, "timeout": 30})
        msg = (r.get("stderr") or r.get("stdout") or "").strip() if isinstance(r, dict) else str(r)
        out.append(
            "%s: exit %s %s" % (op, r.get("exit_code") if isinstance(r, dict) else "?", msg[:120])
        )
        return out

    if recipe["kind"] == "action" and recipe["id"] == "close_popups":
        lw, _ = ag.call("list_windows", {})
        n = 0
        for w in lw.get("windows", []) if isinstance(lw, dict) else []:
            if w.get("class") == "#32770":
                ag.call("show_window", {"hwnd": w["hwnd"], "mode": "close"})
                n += 1
        out.append("closed %d dialog(s)" % n)
        return out

    return ["(nothing to do)"]
