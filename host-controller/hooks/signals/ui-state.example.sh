#!/bin/bash
# Example signal hook: UI state notifications
#
# This script is called when AutoIt sends GET /api/v1/signal/ui-state?value=ready
# Input: First argument is the value
#
# To use:
# 1. Copy to ui-state.sh: cp ui-state.example.sh ui-state.sh
# 2. Make executable: chmod +x ui-state.sh
# 3. Customize logic below
# 4. Restart controller

VALUE="$1"

# Example: Log to file
echo "$(date '+%Y-%m-%d %H:%M:%S') - UI state: $VALUE" >> /tmp/ui-state.log

# Example: Trigger external action
# if [ "$VALUE" = "error" ]; then
#     # Send notification, restart service, etc.
#     echo "Error detected!" | mail -s "UI Error" admin@example.com
# fi
