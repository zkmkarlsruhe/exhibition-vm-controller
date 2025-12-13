#!/bin/bash
# Example poll hook: Button state
#
# This script is called when AutoIt polls GET /api/v1/poll/button
# Output: Just echo the state as a single word
#
# To use:
# 1. Copy to button.sh: cp button.example.sh button.sh
# 2. Make executable: chmod +x button.sh
# 3. Customize logic below
# 4. Restart controller

# Example: Read from GPIO pin, serial device, file, etc.
# if [ -f /sys/class/gpio/gpio17/value ]; then
#     value=$(cat /sys/class/gpio/gpio17/value)
#     if [ "$value" = "1" ]; then
#         echo "pressed"
#     else
#         echo "released"
#     fi
# else
#     echo "released"
# fi

# Placeholder: return fixed value
echo "released"
