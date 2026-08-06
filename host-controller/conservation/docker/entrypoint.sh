#!/usr/bin/env bash
# Dispatch the conservation tools. The container is one image; pick a tool:
#   docker run ... cons-toolkit proxy                  # mitmproxy archive server
#   docker run ... cons-toolkit capture "CYF-Subfusion"
#   docker run -i ... cons-toolkit mcp "CYF-Example"   # MCP over stdio
#   docker run ... cons-toolkit install-ca --vm "CYF-Example"
#   docker run ... cons-toolkit shell
set -e
cmd="${1:-proxy}"; shift || true

case "$cmd" in
  proxy)
    # regular = explicit proxy (guest points WinINet at the host:port);
    # set CONS_MODE=transparent for transparent interception.
    exec mitmdump --mode "${CONS_MODE:-regular}" \
        --listen-port "${CONS_PORT:-8080}" \
        --set confdir="${CONS_CONFDIR:-/cadir}" \
        -s /opt/conservation/proxy/serve_archive.py "$@"
    ;;
  mcp)        exec python3 /opt/conservation/host_mcp.py --vm "$@" ;;
  capture)    exec python3 /opt/conservation/capture_traffic.py "$@" ;;
  webui)      exec python3 /opt/conservation/traffic_webui.py "$@" ;;
  install-ca)    exec python3 /opt/conservation/proxy/install_proxy_ca.py "$@" ;;
  install-trust) exec python3 /opt/conservation/proxy/install_guest_trust.py "$@" ;;
  kiosk)         exec python3 /opt/conservation/kiosk/webui.py "$@" ;;
  dns)           exec python3 /opt/conservation/proxy/dns_responder.py "$@" ;;
  archive)       exec python3 /opt/conservation/proxy/archive_server.py "$@" ;;
  transparent-on|transparent-off)
    # ask the broker (via the virsh shim) to add/remove the per-VM iptables REDIRECT
    vm="$1"
    ip=$(virsh domifaddr "$vm" --source agent | awk '/ipv4/{print $4}' | cut -d/ -f1 | grep -v '^127' | head -1)
    [ -z "$ip" ] && { echo "could not resolve guest IP for $vm" >&2; exit 1; }
    exec virsh "$cmd" "$ip" "${CONS_PORT:-8080}"
    ;;
  shell|bash) exec bash "$@" ;;
  *)          exec "$cmd" "$@" ;;
esac
