#!/usr/bin/env python3
"""
Mock heartbeat sender for testing the Exhibition VM Controller.

This script simulates a guest VM sending heartbeat signals to the host controller.
Use this to test the auto-revert prevention and heartbeat monitoring functionality.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
License: MIT
"""

import time
import requests
import sys
from datetime import datetime


def send_heartbeat(api_url: str = "http://localhost:8000") -> dict:
    """Send a heartbeat signal to the controller API."""
    try:
        response = requests.post(f"{api_url}/api/v1/heartbeat", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"ERROR: Failed to send heartbeat: {e}")
        return None


def get_status(api_url: str = "http://localhost:8000") -> dict:
    """Get current system status."""
    try:
        response = requests.get(f"{api_url}/api/v1/status", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"ERROR: Failed to get status: {e}")
        return None


def main():
    """Main loop - send heartbeats every second."""
    api_url = "http://localhost:8000"
    interval = 1.0  # Send heartbeat every 1 second

    print("=" * 60)
    print("Exhibition VM Controller - Mock Heartbeat Sender")
    print("=" * 60)
    print(f"API URL: {api_url}")
    print(f"Interval: {interval}s")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print()

    # Get initial status
    status = get_status(api_url)
    if status:
        print(f"VM: {status['vm_name']}")
        print(f"VM State: {status['vm_state']}")
        print(
            f"Heartbeat Monitoring: {'enabled' if status['heartbeat']['enabled'] else 'disabled'}"
        )
        print(f"Auto-revert: {'enabled' if status['auto_revert_enabled'] else 'disabled'}")
        print(f"Heartbeat Timeout: {status['heartbeat']['timeout']}s")
        print()

    heartbeat_count = 0

    try:
        while True:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result = send_heartbeat(api_url)

            if result:
                heartbeat_count += 1
                enabled = result["details"]["enabled"]
                time_since = result["details"]["time_since_heartbeat"]
                is_timed_out = result["details"]["is_timed_out"]

                status_icon = "✓" if enabled else "⏸"
                timeout_warning = " [TIMEOUT!]" if is_timed_out else ""

                print(
                    f"{status_icon} [{timestamp}] Heartbeat #{heartbeat_count} sent "
                    f"| Monitoring: {'ON ' if enabled else 'OFF'} "
                    f"| Last: {time_since:.2f}s ago{timeout_warning}"
                )
            else:
                print(f"✗ [{timestamp}] Failed to send heartbeat #{heartbeat_count + 1}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print()
        print("=" * 60)
        print(f"Stopped. Sent {heartbeat_count} heartbeats.")
        print("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
