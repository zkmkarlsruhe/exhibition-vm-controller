#!/usr/bin/env bash
# Start the VM controller, killing any existing instances first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_DIR="$SCRIPT_DIR/host-controller"
CONFIG="$SCRIPT_DIR/deployment/configs/example.yaml"

# Kill any running vm_controller processes
echo "Stopping existing VM controller instances..."
pkill -f 'python.*vm_controller\.api' 2>/dev/null && echo "Killed existing processes." || echo "No existing processes found."

# Brief pause to let ports release
sleep 1

echo "Starting VM controller..."
cd "$HOST_DIR"
exec .venv/bin/python -m vm_controller.api --config "$CONFIG"
