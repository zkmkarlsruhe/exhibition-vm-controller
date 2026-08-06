#!/usr/bin/env bash
# Cross-compile the legacy in-guest agent to a single Windows XP-compatible
# .exe — from Linux, no Windows build box, no runtime to install in the guest.
#
# IMPORTANT — you MUST build with the Go 1.10.x toolchain (the last with
# Windows XP support). Modern Go (>=1.11) stamps a Windows-7 minimum version
# (PE MinSubsystemVersion 6.1) into the binary, and the XP loader then rejects
# it outright with "Exec format error" — verified on a real XP guest. Go 1.10
# stamps 4.0, which XP accepts.
#
#   curl -LO https://go.dev/dl/go1.10.8.linux-amd64.tar.gz
#   mkdir -p /tmp/go110 && tar -C /tmp/go110 --strip-components=1 -xzf go1.10.8.linux-amd64.tar.gz
#   GOROOT=/tmp/go110 PATH=/tmp/go110/bin:$PATH ./build.sh
#
# A windows/386 binary runs Windows XP through Windows 10.
set -euo pipefail
cd "$(dirname "$0")"

# -H windowsgui = GUI subsystem: the exe runs with NO console window, so it sits
# silently in the background (it's a TCP server, it needs no console).
GOOS=windows GOARCH=386 CGO_ENABLED=0 go build -ldflags "-s -w -H windowsgui" \
	-o legacy-agent.exe legacy_agent.go winapi_windows.go
echo "built: $(pwd)/legacy-agent.exe"

# Fail loudly if the PE header isn't XP-compatible, so we never ship a dud exe.
python3 - <<'PY'
import struct, sys
d = open("legacy-agent.exe", "rb").read()
opt = struct.unpack_from("<I", d, 0x3c)[0] + 24
maj, minr = struct.unpack_from("<HH", d, opt + 48)  # MinSubsystemVersion
print("PE MinSubsystemVersion: %d.%d" % (maj, minr))
if (maj, minr) > (5, 1):
    sys.exit("ERROR: %d.%d > 5.1 — Windows XP will reject this exe. "
             "Rebuild with the Go 1.10.x toolchain (see header)." % (maj, minr))
print("XP-compatible (<= 5.1). OK.")
PY

file legacy-agent.exe 2>/dev/null || true
