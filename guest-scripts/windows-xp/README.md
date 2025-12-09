# Windows XP Guest Scripts

AutoIT monitoring scripts for Windows XP virtual machines.

## Overview

These scripts run inside the Windows XP guest VM and communicate with the host controller to:
1. Send periodic heartbeat signals
2. Monitor system idle time
3. Watch critical processes
4. Detect application-specific error conditions

## Prerequisites

- **AutoIT**: Install AutoIT v3 in your Windows XP VM
  - Download from: https://www.autoitscript.com/
  - Or use the version from the Internet Archive
- **Network**: VM must have network connectivity to host
- **QEMU Guest Agent**: Recommended (install from VirtIO drivers ISO)

## Installation

1. **Copy scripts to VM**:
   - Copy all `.au3` files to a folder in the VM (e.g., `C:\Monitoring\`)

2. **Configure host URL**:
   - Edit each script and change the `$host` variable to your host's IP/hostname
   - Default assumes `192.168.122.1` (typical libvirt default network gateway)
   - Replace `your-host-ip` or `exhibition-vm` with actual host address

3. **Compile scripts** (optional but recommended):
   ```
   Right-click on .au3 file → Compile Script (x86)
   ```
   This creates `.exe` files that can run without AutoIT installed.

4. **Set to run at startup**:
   - Place compiled `.exe` files (or `.au3` scripts) in:
     ```
     C:\Documents and Settings\All Users\Start Menu\Programs\Startup
     ```
   - Or create shortcuts in the Startup folder

## Scripts

### heartbeat.au3

**Purpose**: Sends periodic heartbeat to prove VM is alive.

**Configuration**:
```autoit
Local $host = "192.168.122.1"  ; Change to your host IP
Local $url = "http://" & $host & ":8000/api/v1/heartbeat"
```

**Behavior**:
- Waits for network connectivity
- Sends HTTP GET to heartbeat endpoint every 1 second
- Checks that other monitoring scripts are running
- Exits if critical processes are missing

**Dependencies**: Requires `process-watchdog.exe` and `idle-monitor.exe` to be running.

---

### idle-monitor.au3

**Purpose**: Detects user inactivity and triggers VM restart.

**Configuration**:
```autoit
Local $host = "192.168.122.1"  ; Change to your host IP
Local $url = "http://" & $host & ":8000/api/v1/vm/restart"
Global Const $IDLE_TIME_THRESHOLD = 15 * 60 * 1000  ; 15 minutes
```

**Behavior**:
- Checks system idle time every 5 seconds using `_Timer_GetIdleTime()`
- If idle > threshold, sends restart request to host
- Exits after triggering restart (will restart with VM)

**Requires**: `#include <Timers.au3>`

---

### process-watchdog.au3

**Purpose**: Monitors your artwork application and manages windows.

**Configuration**:
```autoit
$applicationPath = "C:\Path\To\Your\Application.exe"
$applicationUrl = "http://your-application/url"  ; If web-based
$expectedWindowTitle = "Your Application"

; Windows that are allowed to stay open
Global $allowedWindows[5] = ["Your Application", "SciTE", "Program Manager",
                               "Your App Window", "Windows Task Manager"]
```

**Behavior**:
- Launches your application
- Waits for application window to appear
- Monitors window state (keeps maximized and focused)
- Closes unauthorized windows
- Triggers VM restart if application closes unexpectedly

**Customization Required**: This script is template-based and must be adapted to your specific application.

---

### button-detector.au3

**Purpose**: Example of application-specific interaction (physical button simulation).

**Configuration**:
```autoit
Local $host = "192.168.122.1"
Local $url = "http://" & $host & ":8000/api/v1/button-status"
Local $x = @DesktopWidth / 2  ; Button X coordinate
Local $y = 100                 ; Button Y coordinate
Local $buttonColorR = 246      ; RGB color detection
Local $buttonColorG = 246
Local $buttonColorB = 243
```

**Behavior**:
- Checks pixel color at coordinates to detect button
- Polls host API for button press signal
- Simulates mouse click when both conditions met

**Use Case**: Physical hardware button integration (Arduino, etc.) where host receives hardware signal and guest script performs the actual UI interaction.

---

### run.au3

**Purpose**: Restart all monitoring scripts.

**Behavior**:
- Closes all monitoring processes
- Waits 5 seconds
- Restarts all monitoring processes

**Use**: Manual recovery or testing. Can be called from process-watchdog if needed.

---

## Network Configuration

All scripts assume the host is reachable at `192.168.122.1` (default libvirt network gateway).

To find your host IP from inside the VM:
```cmd
ipconfig
```
Look for "Default Gateway" - this is typically your host.

Or test connectivity:
```cmd
ping 192.168.122.1
```

## Troubleshooting

### Scripts won't start
- Check AutoIT is installed
- Try running manually first (double-click `.au3` file)
- Check Windows firewall isn't blocking

### Network errors
- Verify host IP is correct
- Test with `ping` from guest
- Check host controller API is running:
  ```bash
  curl http://localhost:8000/api/v1/status
  ```

### Heartbeat timeout
- Check heartbeat script is running in VM
- Verify network connectivity
- Check host controller logs
- Ensure heartbeat URL is correct

### Process monitoring issues
- Edit `process-watchdog.au3` to match your application
- Update `$allowedWindows` array
- Test window title detection:
  ```autoit
  MsgBox(0, "Active Window", WinGetTitle("[ACTIVE]"))
  ```

## Testing

Before creating the "ready" snapshot:

1. **Test heartbeat**:
   ```bash
   # On host
   curl http://localhost:8000/api/v1/heartbeat/status
   ```
   Should show recent heartbeat timestamp.

2. **Test idle detection**:
   - Leave VM idle for configured time
   - Should trigger automatic restart

3. **Test process monitoring**:
   - Close your application
   - Should detect and trigger restart

4. **Check all processes running**:
   ```cmd
   tasklist | find "heartbeat"
   tasklist | find "idle-monitor"
   tasklist | find "process-watchdog"
   ```

## Customization Guide

1. **Adjust timeouts**:
   - Heartbeat interval: Change `Sleep(1000)` in heartbeat.au3
   - Idle timeout: Change `$IDLE_TIME_THRESHOLD` in idle-monitor.au3

2. **Add custom checks**:
   - Create new script based on existing templates
   - Call host API to report custom error conditions
   - Add to startup and to process list in heartbeat.au3

3. **Application-specific monitoring**:
   - Modify process-watchdog.au3 for your application
   - Add pixel color checks, window state checks, etc.
   - See button-detector.au3 for examples

## Security Considerations

- Scripts communicate over HTTP (not HTTPS) - ensure isolated network
- Host API should not be exposed to public network
- VM network should be internal-only or NATed

## License

These scripts are part of the Exhibition VM Controller project.
See LICENSE file in repository root.

## Author

Marc Schütze (mschuetze@zkm.de)
ZKM | Center for Art and Media Karlsruhe
