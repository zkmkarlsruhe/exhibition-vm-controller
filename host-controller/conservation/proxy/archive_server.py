#!/usr/bin/env python3
"""Archive server — serve dead-host content from the archive, fronted by nginx.

The no-privilege path for transparent-ish conservation: the guest's hosts file
(or DNS) points a dead host at the gateway, the **existing nginx on :80**
reverse-proxies the request here (an unprivileged high port), and we return the
archived response keyed by the **Host header** + path. No iptables, no sudoers,
no root, no privileged bind — nginx already owns :80.

Misses return a stub (default 404). For recording live servers, use mitmproxy
(serve_archive.py); this server is serve-only.

    archive_server.py --archive /archive --port 8081
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import archive_store

ARCHIVE = "/archive"
STUB = 404
LOGFILE = ""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _serve(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        hit = archive_store.lookup(ARCHIVE, host, self.path)
        if hit:
            self._log(host, "archive %d" % hit["status"])
            self.send_response(hit["status"])
            self.send_header("Content-Type", hit["content_type"])
            self.send_header("Content-Length", str(len(hit["body"])))
            self.send_header("X-Conserved", "archive")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(hit["body"])
        else:
            self._log(host, "stub %d" % STUB)
            body = b"conserved: no archive entry for this host/path\n"
            self.send_response(STUB)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Conserved", "stub")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    do_GET = _serve
    do_HEAD = _serve
    do_POST = _serve

    def _log(self, host, disp):
        line = "%s %s%s -> %s" % (self.command, host, self.path, disp)
        sys.stderr.write("[archive] " + line + "\n")
        sys.stderr.flush()
        if LOGFILE:
            try:
                with open(LOGFILE, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "method": self.command,
                                "host": host,
                                "path": self.path,
                                "disposition": disp,
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass


def main():
    global ARCHIVE, STUB, LOGFILE
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--archive", default=os.environ.get("CONS_ARCHIVE", "/archive"))
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--stub", type=int, default=int(os.environ.get("CONS_STUB", "404")))
    ap.add_argument("--log", default=os.environ.get("CONS_LOG", ""))
    args = ap.parse_args()
    ARCHIVE, STUB, LOGFILE = args.archive, args.stub, args.log
    sys.stderr.write(
        "archive-server on %s:%d  serving %s (stub %d)\n" % (args.listen, args.port, ARCHIVE, STUB)
    )
    sys.stderr.flush()
    ThreadingHTTPServer((args.listen, args.port), H).serve_forever()


if __name__ == "__main__":
    main()
