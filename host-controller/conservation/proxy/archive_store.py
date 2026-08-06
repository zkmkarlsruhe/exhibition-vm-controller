"""Shared archive store — map a request (host + path) to a saved response.

Used by both serve_archive.py (the mitmproxy addon) and archive_server.py (the
nginx-fronted backend), so the on-disk layout never drifts:

    <archive>/<host>/<path...>            response body   (human-curatable)
    <archive>/<host>/<path...>.cons-meta  {"status":..,"content_type":..}
"""

import hashlib
import json
import mimetypes
import os


def _safe(seg):
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "_" for c in seg) or "_"
    # A segment made only of dots ('.', '..', '...') is a path-traversal token, not a filename:
    # '.' whitelisted above means '..' would otherwise survive intact and let a crafted host/path
    # escape the archive root via os.path.join. Neutralise any all-dots segment.
    if set(cleaned) <= {"."}:
        return "_"
    return cleaned


def paths(archive_dir, host, rawpath):
    """(body_path, meta_path) under archive_dir, traversal-safe."""
    host = _safe(host)
    p, _, q = rawpath.partition("?")
    parts = [_safe(s) for s in p.split("/") if s] or ["index"]
    name = parts[-1]
    if q:
        name += "__q_" + hashlib.sha1(q.encode()).hexdigest()[:8]
    body = os.path.join(archive_dir, host, *parts[:-1], name)
    # Defence in depth: after _safe() no segment can traverse, but confirm the composed path is
    # still confined under the archive root and refuse if it somehow escapes.
    root = os.path.abspath(archive_dir)
    if os.path.abspath(body) != root and not os.path.abspath(body).startswith(root + os.sep):
        raise ValueError("archive path escapes root")
    return body, body + ".cons-meta"


def lookup(archive_dir, host, rawpath):
    """Return {status, content_type, body(bytes)} if archived, else None."""
    body, meta = paths(archive_dir, host, rawpath)
    if not os.path.isfile(body):
        return None
    status = 200
    ctype = mimetypes.guess_type(body)[0] or "application/octet-stream"
    if os.path.isfile(meta):
        try:
            m = json.load(open(meta))
            status, ctype = m.get("status", status), m.get("content_type", ctype)
        except (OSError, ValueError):
            pass
    return {"status": status, "content_type": ctype, "body": open(body, "rb").read()}


def save(archive_dir, host, rawpath, status, content_type, content):
    body, meta = paths(archive_dir, host, rawpath)
    os.makedirs(os.path.dirname(body), exist_ok=True)
    with open(body, "wb") as f:
        f.write(content or b"")
    with open(meta, "w") as f:
        json.dump({"status": status, "content_type": content_type}, f)
