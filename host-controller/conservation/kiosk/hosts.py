#!/usr/bin/env python3
"""Edit a guest's hosts file over the agent — point a hostname at the proxy/localhost.

The surgical conservation move for a *known* hardcoded hostname: the artwork
calls e.g. http://artwork.local, so add `192.168.122.1 artwork.local` to the guest's
hosts file and the request lands on the conservation proxy (or whatever serves
that host) — no DNS interception, no iptables, no per-app proxy setting.

Our entries are tagged so the UI manages only its own. Writing the hosts file
needs Administrator (it lives under System32); failures are reported, not hidden.
"""

HOSTS_PATH = r"C:\Windows\System32\drivers\etc\hosts"
TAG = "# zkm-conservation"


def _read(ag):
    """Return (text, err). ``err`` is truthy when the guest read failed.

    Callers MUST abort on a truthy err: treating a failed read as an empty hosts file and then
    writing back would CLOBBER the guest's real hosts file with only our partial entries.
    """
    out, err = ag.call("read_file", {"path": HOSTS_PATH})
    if err:
        return "", err
    if not isinstance(out, dict):
        return "", "unexpected read_file response: %r" % (out,)
    return out.get("text", ""), None


def parse(ag):
    """Return [{ip, host, ours}] for active (non-comment) hosts lines."""
    entries = []
    text, err = _read(ag)
    if err:
        return entries  # can't read → report nothing rather than a misleading empty file
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) >= 2:
            entries.append({"ip": parts[0], "host": parts[1], "ours": TAG in line})
    return entries


def _is_ours_for(line, host):
    p = line.split()
    return TAG in line and len(p) >= 2 and p[1].lower() == host.lower()


def _write(ag, lines):
    body = "\r\n".join(lines).rstrip("\r\n") + "\r\n"
    res, err = ag.call("write_file", {"path": HOSTS_PATH, "content": body})
    if err:
        return False, "write failed (need Administrator?): %s" % res
    return True, "ok"


def add(ag, host, ip):
    host = host.strip()
    if not host or not ip.strip():
        return False, "host and ip required"
    current, err = _read(ag)
    if err:
        # Abort — never rewrite the hosts file from a failed/partial read (would clobber it).
        return False, "read failed (need Administrator?): %s" % err
    lines = [ln for ln in current.splitlines() if not _is_ours_for(ln, host)]
    lines.append("%s %s %s" % (ip.strip(), host, TAG))
    return _write(ag, lines)


def remove(ag, host):
    current, err = _read(ag)
    if err:
        return False, "read failed (need Administrator?): %s" % err
    lines = [ln for ln in current.splitlines() if not _is_ours_for(ln, host)]
    return _write(ag, lines)
