"""
Example plugin: Arduino button polling.

This example shows how to create a poll provider that reads button state
from an Arduino or other external device.

To use:
1. Copy to plugins/button.py (remove .example.py)
2. Modify get_button_state() to read from your device
3. Restart the controller

AutoIt can then poll: GET /api/v1/poll/button
Returns: "pressed" or "released"
"""

import logging
import serial  # pip install pyserial

logger = logging.getLogger(__name__)


def get_button_state() -> str:
    """
    Read button state from Arduino.

    Returns:
        "pressed" or "released"
    """
    try:
        # Example: Read from serial port
        # with serial.Serial('/dev/ttyUSB0', 9600, timeout=1) as ser:
        #     data = ser.readline().decode().strip()
        #     return "pressed" if data == "1" else "released"

        # Placeholder: return fixed value
        return "released"

    except Exception as e:
        logger.error(f"Error reading button state: {e}")
        return "released"


def setup(registry):
    """
    Register this plugin with the registry.

    Args:
        registry: PluginRegistry instance
    """
    registry.register_poll_provider("button", get_button_state)
    logger.info("Arduino button plugin loaded")
