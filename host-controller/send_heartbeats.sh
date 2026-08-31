#!/bin/bash
# Mock heartbeat sender for testing the Exhibition VM Controller
# Sends periodic heartbeat signals to prevent auto-revert

API_URL="${1:-http://localhost:8000}"
INTERVAL="${2:-1}"

echo "========================================================"
echo "Exhibition VM Controller - Mock Heartbeat Sender"
echo "========================================================"
echo "API URL: $API_URL"
echo "Interval: ${INTERVAL}s"
echo "Press Ctrl+C to stop"
echo "========================================================"
echo ""

# Get initial status
echo "Initial status:"
curl -s "$API_URL/api/v1/status" | python3 -m json.tool | grep -E '"vm_name"|"vm_state"|"enabled"|"timeout"'
echo ""

COUNT=0

# Send heartbeats in a loop
while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    RESPONSE=$(curl -s -X POST "$API_URL/api/v1/heartbeat")

    if [ $? -eq 0 ]; then
        ((COUNT++))
        ENABLED=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['details']['enabled'])" 2>/dev/null)
        TIME_SINCE=$(echo "$RESPONSE" | python3 -c "import sys, json; print('{:.2f}'.format(json.load(sys.stdin)['details']['time_since_heartbeat']))" 2>/dev/null)

        if [ "$ENABLED" = "True" ]; then
            STATUS="✓ ON "
        else
            STATUS="⏸ OFF"
        fi

        echo "$STATUS [$TIMESTAMP] Heartbeat #$COUNT sent | Last: ${TIME_SINCE}s ago"
    else
        echo "✗ [$TIMESTAMP] Failed to send heartbeat #$((COUNT + 1))"
    fi

    sleep "$INTERVAL"
done
