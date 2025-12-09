# Heartbeat Protocol

The heartbeat protocol is the primary communication mechanism between guest VMs and the host controller. It enables continuous health monitoring without modifying artwork code.

## Overview

The protocol is intentionally simple:
- **Transport**: HTTP/1.1
- **Format**: Query parameters or JSON
- **Direction**: Guest → Host (unidirectional)
- **Frequency**: Configurable (default: 1 second)
- **Timeout**: Configurable (default: 10 seconds)

## Communication Architecture

```
┌─────────────────────────────────────┐
│         Guest VM                    │
│                                     │
│  ┌───────────────────────────────┐ │
│  │   Monitoring Scripts          │ │
│  │   • heartbeat.au3             │ │
│  │   • idle-monitor.au3          │ │
│  │   • process-watchdog.au3      │ │
│  │   • custom checks             │ │
│  └───────────┬───────────────────┘ │
│              │                      │
│              │ HTTP GET/POST        │
└──────────────┼──────────────────────┘
               │
               │ Virtual Network (192.168.122.0/24)
               │
┌──────────────▼──────────────────────┐
│     Host Controller (FastAPI)       │
│     Listening on 192.168.122.1:8000 │
│                                     │
│  Endpoints:                         │
│  • /api/v1/heartbeat                │
│  • /api/v1/revert/request           │
│  • /api/v1/idle/reset               │
└─────────────────────────────────────┘
```

## Endpoint Specifications

### 1. Heartbeat Signal

**Purpose**: Inform host that guest is alive and operational.

**Endpoint**: `GET /api/v1/heartbeat`

**Parameters**:
- `status` (optional): `ok`, `warning`, or custom status string
- `message` (optional): Additional context

**Example Request**:
```bash
curl "http://192.168.122.1:8000/api/v1/heartbeat?status=ok"
```

**Response**:
```json
{
  "status": "success",
  "message": "Heartbeat received",
  "timestamp": "2025-12-09T10:30:45.123456"
}
```

**Behavior**:
- Updates `last_heartbeat` timestamp on host
- Resets timeout counter
- Logs receipt (optional, configurable verbosity)

### 2. Revert Request

**Purpose**: Guest explicitly requests a snapshot revert.

**Endpoint**: `POST /api/v1/revert/request`

**Parameters**:
- `reason` (required): Why revert is needed
- `severity` (optional): `error`, `warning`, `info`

**Example Request**:
```bash
curl -X POST "http://192.168.122.1:8000/api/v1/revert/request" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Process crash detected", "severity": "error"}'
```

**Response**:
```json
{
  "status": "accepted",
  "message": "Revert queued",
  "will_revert_in_seconds": 2
}
```

**Behavior**:
- Immediately triggers snapshot revert if auto-revert is enabled
- Logs the reason for forensic analysis
- Returns quickly (does not wait for revert to complete)

### 3. Idle Reset

**Purpose**: Guest reports idle timeout, requests reset.

**Endpoint**: `POST /api/v1/idle/reset`

**Parameters**:
- `idle_duration` (optional): Seconds of inactivity

**Example Request**:
```bash
curl -X POST "http://192.168.122.1:8000/api/v1/idle/reset" \
  -H "Content-Type: application/json" \
  -d '{"idle_duration": 720}'
```

**Response**:
```json
{
  "status": "accepted",
  "message": "Idle reset triggered"
}
```

**Behavior**:
- Triggers snapshot revert if idle_timeout is configured
- Distinguishes idle resets from error resets in logs

## Heartbeat Implementation Examples

### AutoIt (Windows)

**Basic Heartbeat Loop**:

```autoit
#include <Inet.au3>

Global $HOST_URL = "http://192.168.122.1:8000"
Global $HEARTBEAT_INTERVAL = 1000  ; milliseconds

While True
    $response = InetGet($HOST_URL & "/api/v1/heartbeat?status=ok", "", 1)
    Sleep($HEARTBEAT_INTERVAL)
WEnd
```

**With Error Handling**:

```autoit
#include <Inet.au3>

Global $HOST_URL = "http://192.168.122.1:8000"
Global $HEARTBEAT_INTERVAL = 1000
Global $MAX_FAILURES = 5
Global $failures = 0

While True
    $response = InetGet($HOST_URL & "/api/v1/heartbeat?status=ok", "", 1)

    If @error Then
        $failures += 1
        If $failures >= $MAX_FAILURES Then
            ; Cannot reach host, something is seriously wrong
            Exit
        EndIf
    Else
        $failures = 0
    EndIf

    Sleep($HEARTBEAT_INTERVAL)
WEnd
```

### Python (Linux Guests)

```python
import requests
import time

HOST_URL = "http://192.168.122.1:8000"
HEARTBEAT_INTERVAL = 1  # seconds

while True:
    try:
        response = requests.get(
            f"{HOST_URL}/api/v1/heartbeat",
            params={"status": "ok"},
            timeout=2
        )
        response.raise_for_status()
    except requests.RequestException as e:
        # Log but continue
        print(f"Heartbeat failed: {e}")

    time.sleep(HEARTBEAT_INTERVAL)
```

### Shell Script (Linux/Mac)

```bash
#!/bin/bash

HOST_URL="http://192.168.122.1:8000"
HEARTBEAT_INTERVAL=1

while true; do
    curl -s "$HOST_URL/api/v1/heartbeat?status=ok" > /dev/null
    sleep $HEARTBEAT_INTERVAL
done
```

## Process Monitoring Implementation

**AutoIt Process Watchdog**:

```autoit
#include <Inet.au3>

Global $HOST_URL = "http://192.168.122.1:8000"
Global $PROCESS_NAME = "artwork.exe"
Global $CHECK_INTERVAL = 5000  ; Check every 5 seconds

While True
    If Not ProcessExists($PROCESS_NAME) Then
        ; Process not running, request revert
        InetGet($HOST_URL & "/api/v1/revert/request?reason=process_crash", "", 1)
        Exit
    EndIf
    Sleep($CHECK_INTERVAL)
WEnd
```

## Idle Detection Implementation

**AutoIt Idle Monitor**:

```autoit
#include <Inet.au3>

Global $HOST_URL = "http://192.168.122.1:8000"
Global $IDLE_TIMEOUT = 720 * 1000  ; 12 minutes in milliseconds
Global $CHECK_INTERVAL = 1000

While True
    $idleTime = _Timer_GetIdleTime()

    If $idleTime >= $IDLE_TIMEOUT Then
        ; User has been idle too long
        InetGet($HOST_URL & "/api/v1/idle/reset?idle_duration=" & Int($idleTime/1000), "", 1)

        ; Wait for revert to happen
        Sleep(10000)
        Exit
    EndIf

    Sleep($CHECK_INTERVAL)
WEnd

Func _Timer_GetIdleTime()
    ; Windows API call to get milliseconds since last input
    Local $struct = DllStructCreate("uint;dword")
    DllStructSetData($struct, 1, DllStructGetSize($struct))
    DllCall("user32.dll", "int", "GetLastInputInfo", "ptr", DllStructGetPtr($struct))
    Local $lastInput = DllStructGetData($struct, 2)
    Return @MSEC - $lastInput
EndFunc
```

## Protocol State Machine

### Host-Side State Machine

```
┌─────────────────┐
│   INITIALIZING  │
└────────┬────────┘
         │
         │ VM Started
         ▼
┌─────────────────┐
│   WAITING_FOR_  │
│   FIRST_BEAT    │
└────────┬────────┘
         │
         │ Heartbeat Received
         ▼
┌─────────────────┐
│     HEALTHY     │◄────────┐
└────────┬────────┘         │
         │                  │
         │ Timeout          │ Heartbeat
         │ (10s)            │ Received
         ▼                  │
┌─────────────────┐         │
│   UNHEALTHY     │         │
└────────┬────────┘         │
         │                  │
         │ Trigger Revert   │
         ▼                  │
┌─────────────────┐         │
│   REVERTING     │         │
└────────┬────────┘         │
         │                  │
         │ Revert Complete  │
         └──────────────────┘
```

## Network Configuration

### Default Network (NAT)

Most VMs use NAT networking with default libvirt network:

- **Network**: `192.168.122.0/24`
- **Host IP**: `192.168.122.1`
- **Guest DHCP**: `192.168.122.2` - `192.168.122.254`

Guest scripts should use `192.168.122.1` as host address.

### Custom Network

If using a custom network:

```bash
virsh net-list --all
virsh net-dumpxml YOUR_NETWORK
```

Adjust `$HOST_URL` in guest scripts accordingly.

### Bridged Network

If using bridged networking, determine host IP with:

```bash
ip addr show br0
```

Update guest scripts with the correct IP.

## Debugging the Protocol

### Capture Traffic with tcpdump

On host:

```bash
sudo tcpdump -i virbr0 -A port 8000
```

### Test from Host

Simulate guest heartbeat from host:

```bash
curl "http://192.168.122.1:8000/api/v1/heartbeat?status=ok"
```

### Test from Guest

Open browser in guest and visit:
```
http://192.168.122.1:8000/api/v1/heartbeat?status=ok
```

Should see JSON response.

### Check Host Controller Logs

```bash
journalctl -u exhibition-vm-controller -f
```

Or if running manually:

```bash
cd host-controller
poetry run python -m vm_controller.api
```

Watch console output for heartbeat receipts.

## Failure Scenarios

### Guest Cannot Reach Host

**Symptoms**:
- Guest scripts fail silently
- Host never receives heartbeat
- Timeout triggers revert

**Diagnosis**:
```bash
# In guest, test connectivity
ping 192.168.122.1
telnet 192.168.122.1 8000
```

**Solutions**:
- Check firewall rules on host
- Verify libvirt network is active: `virsh net-list`
- Check host controller is running: `ps aux | grep vm_controller`

### Host Cannot Reach Guest Agent

**Symptoms**:
- Heartbeat works, but `check_qemu_agent` fails
- Warnings in host logs

**Diagnosis**:
```bash
virsh qemu-agent-command YOUR_VM_NAME '{"execute":"guest-ping"}'
```

**Solutions**:
- Ensure QEMU guest agent is installed in VM
- Verify channel device exists in VM config
- Restart VM

### Heartbeat Too Slow

**Symptoms**:
- Frequent timeout despite healthy system
- Sporadic reverts

**Solutions**:
- Increase `heartbeat_timeout` in config
- Reduce `HEARTBEAT_INTERVAL` in guest script
- Check for network latency

## Performance Considerations

**Heartbeat Overhead**:
- Network: ~100 bytes per heartbeat
- CPU: Negligible (<0.1% on both guest and host)
- Per second: ~86,400 requests per day

**Scaling**:
- Single host can handle 100+ guests (not recommended for isolation reasons)
- FastAPI is async, handles concurrent requests efficiently

**Optimization**:
- Use GET instead of POST for heartbeat (more cacheable)
- Disable verbose logging in production
- Use local logging instead of remote logging

## Security Considerations

**Network Isolation**:
- Default NAT network isolates VM from external network
- Only host can communicate with guest

**No Authentication**:
- Protocol has no authentication
- Assumes trusted guest-host relationship
- Acceptable for isolated museum environments

**Firewall**:
```bash
# Allow only specific VM access (optional)
sudo iptables -A INPUT -i virbr0 -p tcp --dport 8000 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8000 -j DROP
```

## Advanced: Custom Health Checks

You can extend the protocol with custom endpoints:

**Example: Artwork-Specific Check**:

```python
# In host controller (api.py)
@app.get("/api/v1/custom/artwork_status")
async def artwork_status(fps: int, frame: int):
    # Custom logic for artwork-specific health
    if fps < 10:
        trigger_revert("Low FPS detected")
    return {"status": "ok"}
```

**Guest script**:
```autoit
$fps = GetCurrentFPS()
$frame = GetCurrentFrame()
InetGet($HOST_URL & "/api/v1/custom/artwork_status?fps=" & $fps & "&frame=" & $frame, "", 1)
```

## Summary

The heartbeat protocol provides:
- **Simple**: HTTP GET requests, easy to implement in any language
- **Reliable**: Unidirectional, no complex handshakes
- **Flexible**: Extensible with custom endpoints
- **Debuggable**: Standard HTTP tools work everywhere
- **Universal**: Works with any OS that can make HTTP requests

For most use cases, the basic heartbeat is sufficient. Custom checks can be added for artwork-specific requirements.
