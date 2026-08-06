"""mitmproxy addon — serve a legacy artwork's network calls from a local archive.

The conservation core of the proxy layer. For every request the artwork makes:

  1. **Serve** it from the local archive if we have it (dead server → revived).
  2. Otherwise, if pass-through is on, let it reach the live upstream — and if
     record is on, **save** the response into the archive (so it works offline
     next time). This auto-builds the archive from whatever is still alive.
  3. Otherwise return a **stub** (default 404) so the artwork fails gracefully
     instead of hanging.

Every request is logged, so the operator can see exactly what the artwork
reaches for and curate the archive.

Archive layout (human-curatable):
    <archive>/<host>/<path...>            response body
    <archive>/<host>/<path...>.cons-meta  {"status":..,"content_type":..}

Config via env:
    CONS_ARCHIVE   archive dir          (default /archive)
    CONS_RECORD    save live responses  (default 1)
    CONS_PASSTHRU  try live upstream    (default 1)
    CONS_STUB      stub status on miss  (default 404)
    CONS_LOG       JSONL request log    (optional)
"""

import json
import os

from mitmproxy import ctx, http

import archive_store

ARCHIVE = os.environ.get("CONS_ARCHIVE", "/archive")
RECORD = os.environ.get("CONS_RECORD", "1") not in ("0", "false", "")
PASSTHRU = os.environ.get("CONS_PASSTHRU", "1") not in ("0", "false", "")
STUB = int(os.environ.get("CONS_STUB", "404"))
LOGFILE = os.environ.get("CONS_LOG", "")


def _log(flow, disposition):
    line = "%s %s -> %s" % (flow.request.method, flow.request.pretty_url, disposition)
    ctx.log.info("[conserve] " + line)
    if LOGFILE:
        try:
            with open(LOGFILE, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "method": flow.request.method,
                            "url": flow.request.pretty_url,
                            "host": flow.request.pretty_host,
                            "disposition": disposition,
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass


class ServeArchive:
    def request(self, flow: http.HTTPFlow):
        hit = archive_store.lookup(ARCHIVE, flow.request.pretty_host, flow.request.path)
        if hit:
            flow.response = http.Response.make(
                hit["status"],
                hit["body"],
                {"Content-Type": hit["content_type"], "X-Conserved": "archive"},
            )
            _log(flow, "archive (%d, %d bytes)" % (hit["status"], len(hit["body"])))
            return
        if not PASSTHRU:
            flow.response = http.Response.make(
                STUB,
                b"conserved: no archive entry for this request\n",
                {"Content-Type": "text/plain", "X-Conserved": "stub"},
            )
            _log(flow, "stub %d (no archive, passthrough off)" % STUB)

    def response(self, flow: http.HTTPFlow):
        if flow.response.headers.get("X-Conserved"):
            return  # we synthesised this; nothing to record
        status = flow.response.status_code
        # Never freeze an error response as canonical artwork content: a transient 500 or a 404
        # from a still-flaky upstream would be archived and then served forever in place of the
        # real asset (the archive is served on hit with no re-validation). Only record success /
        # redirects (< 400); leave error responses un-archived so a later good response wins.
        recordable = RECORD and status < 400
        _log(flow, "live %d%s" % (status, " (recorded)" if recordable else ""))
        if not recordable:
            return
        try:
            archive_store.save(
                ARCHIVE,
                flow.request.pretty_host,
                flow.request.path,
                flow.response.status_code,
                flow.response.headers.get("Content-Type", ""),
                flow.response.content,
            )
        except OSError as e:
            ctx.log.warn("[conserve] could not record: %s" % e)


addons = [ServeArchive()]
