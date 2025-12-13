# Mac OS 9 Guest Monitoring Scripts

AppleScript-based monitoring scripts for Exhibition VM Controller on Mac OS 9.

## Overview

This directory contains AppleScript monitoring scripts that run inside Mac OS 9 virtual machines to enable automatic health monitoring and recovery. These scripts replicate the proven Windows XP AutoIt architecture for Mac OS 9 environments.

**Key Features:**
- ❤️  **Heartbeat monitoring** - Sends periodic alive signals to host
- 💤 **Idle detection** - Triggers restart after user inactivity
- 👁️  **Process monitoring** - Watches target application and manages windows
- 🔄 **Automatic recovery** - VM restarts on failure or timeout
- 🎯 **Exhibition-ready** - Designed for long-term unattended operation

## Quick Start

### 1. Installation

1. Copy this entire `mac-os-9` folder to your Mac OS 9 VM
2. Recommended location: `Macintosh HD:Monitoring:`

### 2. Configuration

Edit each `.applescript` file and configure the properties at the top:

```applescript
property hostIP : "192.168.122.1"  -- Your host controller IP
property apiPort : "8000"            -- API port (usually 8000)
```

### 3. Compilation

For each monitoring script:

1. Open in **Script Editor**
2. **File → Save As...**
3. **File Format**: `Application`
4. **Options**: ☑ Stay Open, ☑ Never Show Startup Screen
5. Save to `Macintosh HD:Monitoring:`

### 4. Auto-Start Configuration

1. Create aliases of compiled applications
2. Place in `System Folder:Startup Items:`

```
System Folder:Startup Items:
├── heartbeat alias
├── idle-monitor alias
└── process-watchdog alias
```

### 5. Testing

1. Double-click each monitor to launch
2. Check **Script Editor → Event Log** for messages
3. Verify host controller receives heartbeats
4. Create VM snapshot named "ready"

---

## Script Reference

### heartbeat.applescript ⭐ (Critical)

**Purpose**: Sends periodic heartbeat signals proving VM is alive

**Features**:
- Sends heartbeat every 1 second to `/api/v1/heartbeat`
- Acts as supervisor - checks if other monitors are running
- Exits if critical process missing (triggers VM restart)

**Configuration**:
```applescript
property heartbeatInterval : 1           -- Seconds between heartbeats
property checkProcesses : false          -- Enable process supervision
property processNames : {"idle-monitor"} -- Processes to monitor
```

**When it triggers restart**:
- Never directly - stops sending heartbeats which causes host timeout
- Host timeout (default 15 seconds) triggers automatic VM restart

---

### idle-monitor.applescript

**Purpose**: Detects user inactivity and triggers VM restart

**Features**:
- Monitors for user activity (mouse/keyboard)
- Triggers restart after configurable idle period (default: 15 minutes)
- Uses screen saver activation as idle indicator

**Configuration**:
```applescript
property idleThresholdSeconds : 900      -- Idle timeout (900 = 15 min)
property checkInterval : 5               -- Seconds between checks
property useScreenSaver : true           -- Use screen saver as indicator
```

**When it triggers restart**:
- After `idleThresholdSeconds` of inactivity
- Sends request to `/api/v1/vm/restart`
- Useful for resetting exhibition after visitor leaves

**Setup Notes**:
- Configure Mac OS 9 screen saver to activate before idle threshold
- Example: Screen saver at 14 min, idle restart at 15 min
- Gives visitors visual feedback before restart

---

### process-watchdog.applescript

**Purpose**: Monitors target application and manages windows

**Features**:
- Launches application on startup
- Monitors application process status
- Keeps application window focused and sized correctly
- Triggers VM restart if application quits

**Configuration**:
```applescript
property targetApplication : "Internet Explorer"  -- App to monitor
property launchOnStartup : true                   -- Launch app on start
property keepFocused : true                       -- Keep app frontmost
property setWindowBounds : true                   -- Set window size
property windowBounds : {0, 40, 1024, 768}       -- Window position/size
```

**When it triggers restart**:
- When target application quits unexpectedly
- Sends request to `/api/v1/vm/restart`

**Window Bounds Format**:
- `{left, top, right, bottom}` in pixels
- Example for 1024x768: `{0, 40, 1024, 768}`
- Top value 40 accounts for menu bar

---

### restart-monitors.applescript 🔧

**Purpose**: Utility to restart all monitoring scripts

**Features**:
- Quits all monitoring scripts
- Waits for clean shutdown
- Relaunches all monitors
- For manual recovery and testing

**Usage**:
- Double-click to run
- Not a stay-open application - runs once and quits
- Useful during development and troubleshooting

**Configuration**:
```applescript
property monitoringFolder : "Macintosh HD:Monitoring:"
property monitorScripts : {"heartbeat", "idle-monitor", "process-watchdog"}
```

---

### lib/http-helper.applescript 📚

**Purpose**: Shared HTTP communication library

**Features**:
- Handles HTTP GET requests to host controller API
- Automatic method detection (URL Access Scripting or curl)
- Network connectivity checking
- Used by all monitoring scripts

**HTTP Methods** (automatic fallback):
1. **URL Access Scripting** (built-in, recommended)
2. **curl via shell** (if MPW and curl installed)

**Not used directly** - loaded by other scripts

---

## Architecture

### Communication Flow

```
┌─────────────────────────────────────────┐
│ Mac OS 9 VM                              │
│                                           │
│  ┌──────────────────┐                    │
│  │ heartbeat        │──┐                 │
│  │ (every 1 second) │  │                 │
│  └──────────────────┘  │                 │
│                        │                 │
│  ┌──────────────────┐  │  HTTP GET      │
│  │ idle-monitor     │──┼───────────────────→  Host Controller
│  │ (every 5 seconds)│  │  192.168.122.1:8000
│  └──────────────────┘  │                 │
│                        │                 │
│  ┌──────────────────┐  │                 │
│  │ process-watchdog │──┘                 │
│  │ (every 1 second) │                    │
│  └──────────────────┘                    │
│           ↓                               │
│  ┌──────────────────┐                    │
│  │ Target App       │                    │
│  │ (e.g., browser)  │                    │
│  └──────────────────┘                    │
└─────────────────────────────────────────┘
```

### API Endpoints

| Endpoint | Method | Called By | Purpose |
|----------|--------|-----------|---------|
| `/api/v1/heartbeat` | GET | heartbeat | Send alive signal |
| `/api/v1/vm/restart` | GET | idle-monitor, process-watchdog | Trigger VM restart |
| `/api/v1/status` | GET | (manual testing) | Check system status |

### Failure Recovery

```
Application Quits
       ↓
process-watchdog detects
       ↓
Sends /api/v1/vm/restart
       ↓
Host controller reverts VM to snapshot
       ↓
VM restarts with all monitors
       ↓
Application relaunches
       ↓
Normal operation resumes
```

---

## Network Configuration

### Default Settings

- **Host IP**: `192.168.122.1` (libvirt default gateway)
- **API Port**: `8000`
- **Heartbeat Interval**: 1 second
- **Heartbeat Timeout**: 15 seconds (host side)

### Determining Host IP

**Option 1**: Use libvirt default
```
192.168.122.1
```

**Option 2**: Check from Mac OS 9
1. Apple Menu → Control Panels → TCP/IP
2. Note the "Router" address
3. Use this as host IP

**Option 3**: Check from host
```bash
ip addr show virbr0 | grep inet
```

---

## Compatibility

### Supported Mac OS 9 Versions

| Version | Status | Notes |
|---------|--------|-------|
| Mac OS 9.2.2 | ✅ Fully supported | Recommended, most stable |
| Mac OS 9.1 | ✅ Supported | Good compatibility |
| Mac OS 9.0 | ⚠️ Tested | Some features may be limited |

### Requirements

**Required**:
- AppleScript (included in Mac OS 9)
- URL Access Scripting (included in Mac OS 9)
- TCP/IP networking configured

**Optional**:
- System Events (for process detection) - included in 9.1+
- MPW + curl (for alternative HTTP method)

### Feature Detection

Scripts automatically detect available features and adapt:
- URL Access Scripting preferred (universal)
- curl fallback (if available)
- System Events for process checking (graceful degradation)

---

## Installation Guide

### Step-by-Step Setup

#### 1. Prepare Mac OS 9 VM

1. Install Mac OS 9 in QEMU/KVM
2. Configure networking (NAT or bridge)
3. Verify network connectivity:
   - Ping host from Mac OS 9
   - Test with web browser

#### 2. Transfer Scripts

**Option A**: Via CD-ROM image
```bash
# On host
mkisofs -o monitoring.iso -J -R guest-scripts/mac-os-9/
# Attach ISO to VM
```

**Option B**: Via network share
```bash
# Set up Samba or AFP share
# Mount in Mac OS 9
```

**Option C**: Via floppy images (for small files)

#### 3. Organize Files

Create folder structure:
```
Macintosh HD/
└── Monitoring/
    ├── heartbeat.applescript
    ├── idle-monitor.applescript
    ├── process-watchdog.applescript
    ├── restart-monitors.applescript
    └── lib/
        └── http-helper.applescript
```

#### 4. Configure Scripts

For each `.applescript` file:
1. Open in Script Editor
2. Edit configuration properties:
   - Set `hostIP` to your host controller IP
   - Adjust intervals/thresholds as needed
   - Configure target application (for process-watchdog)
3. Save changes

#### 5. Compile Scripts

For **heartbeat**, **idle-monitor**, and **process-watchdog**:
1. File → Save As...
2. File Format: **Application**
3. Options:
   - ☑ Stay Open
   - ☑ Never Show Startup Screen
   - ☐ Show Startup Screen (unchecked)
4. Save to `Macintosh HD:Monitoring:`

For **restart-monitors**:
1. File → Save As...
2. File Format: **Application**
3. Options:
   - ☐ Stay Open (UNCHECKED - regular app)
   - ☑ Never Show Startup Screen
4. Save to `Macintosh HD:Monitoring:`

#### 6. Test Manually

1. Double-click **heartbeat** application
2. Open Script Editor → Show Event Log
3. Verify "Heartbeat sent successfully" messages
4. Check host controller logs for received heartbeats
5. Repeat for idle-monitor and process-watchdog

#### 7. Configure Auto-Start

1. Select compiled application in Finder
2. File → Make Alias
3. Move alias to `System Folder:Startup Items:`
4. Repeat for each monitor

**Startup Order** (recommended):
1. heartbeat alias
2. process-watchdog alias
3. idle-monitor alias

#### 8. Create Snapshot

1. Configure all monitors as desired
2. Verify all are running correctly
3. Test full cycle (restart, recovery, etc.)
4. When satisfied, create snapshot:

```bash
# On host
virsh snapshot-create-as your-vm-name ready
```

#### 9. Test Full Cycle

1. Stop heartbeat application (should trigger restart)
2. Verify VM reverts to snapshot
3. Verify all monitors auto-start
4. Check logs show successful recovery

---

## Troubleshooting

### Common Issues

#### "Cannot load HTTP helper library"

**Cause**: http-helper.applescript not found or in wrong location

**Solution**:
1. Verify `lib/http-helper.applescript` exists
2. Ensure lib folder is in same parent as monitor scripts
3. Check file permissions

#### "No HTTP method available"

**Cause**: URL Access Scripting and curl both unavailable

**Solution**:
1. Verify URL Access Scripting is installed (should be by default)
2. Try reinstalling Mac OS 9 system software
3. Check System Folder:Extensions for "URL Access"

#### "Network not ready" (loops forever)

**Cause**: Cannot reach host controller

**Solution**:
1. Check VM network configuration
2. Verify host IP is correct
3. Test ping from Mac OS 9:
   - Applications → MacTCP Ping
   - Or use Network Utility
4. Check host controller is running
5. Verify firewall not blocking port 8000

#### Heartbeats not received by host

**Cause**: Various networking or configuration issues

**Solution**:
1. Check host controller logs: `tail -f /var/log/vm-controller.log`
2. Test API manually from Mac OS 9 browser:
   ```
   http://192.168.122.1:8000/api/v1/status
   ```
3. Verify endpoint URLs in scripts match API
4. Check HTTP helper is initializing correctly

#### Application won't launch (process-watchdog)

**Cause**: Application name incorrect or not found

**Solution**:
1. Verify application name matches exactly
2. Use name from Application menu, not filename
3. Try launching application manually first
4. Check `targetApplication` property spelling

#### Scripts won't auto-start on boot

**Cause**: Aliases not in correct location or broken

**Solution**:
1. Verify aliases are in `System Folder:Startup Items:`
2. Check aliases point to correct compiled applications
3. Test by manually triggering startup items:
   - Restart and watch carefully
   - Or use Extensions Manager

#### VM restarts too frequently

**Cause**: Timeouts too aggressive or scripts crashing

**Solution**:
1. Increase heartbeat timeout on host (default 15s)
2. Check scripts for errors in Event Log
3. Increase idle threshold if resetting too soon
4. Review host controller logs for patterns

### Debug Mode

To see detailed logging:

1. Open script in Script Editor
2. Set `verboseLogging` to `true`
3. Recompile
4. Run and watch Event Log

### Testing Individual Components

**Test HTTP helper**:
```applescript
set httpLib to load script file "Macintosh HD:Monitoring:lib:http-helper.applescript"
httpLib's initialize()
httpLib's sendRequest("/api/v1/status")
```

**Test heartbeat manually**:
```bash
# From Mac OS 9 browser, open:
http://192.168.122.1:8000/api/v1/heartbeat
# Should return JSON response
```

**Test application detection**:
```applescript
tell application "System Events"
    exists process "Internet Explorer"
end tell
```

---

## Best Practices

### Configuration

- **Start conservative**: Use longer timeouts during setup
- **Test thoroughly**: Run for several hours before creating snapshot
- **Document changes**: Note any custom configuration
- **Keep backups**: Save configured .applescript source files

### Exhibition Deployment

- **Use descriptive names**: Label VMs clearly
- **Monitor the monitor**: Check host logs regularly
- **Plan for maintenance**: Schedule snapshot updates
- **Document recovery**: Write procedures for staff

### Performance

- **Keep intervals reasonable**: 1s heartbeat is optimal
- **Don't over-log**: Disable verbose logging in production
- **Monitor resources**: Check CPU/memory usage
- **Test under load**: Simulate visitor interaction

### Maintenance

- **Update snapshots**: Refresh after configuration changes
- **Review logs**: Look for patterns indicating issues
- **Test recovery**: Periodically verify restart works
- **Update documentation**: Keep notes on configuration

---

## Advanced Topics

### Custom Application Monitoring

To monitor a custom application:

1. Edit `process-watchdog.applescript`
2. Set `targetApplication` to your app name
3. Adjust `windowBounds` for your display
4. Test launch and window management
5. Verify restart triggers on app quit

### Multiple Applications

To monitor multiple apps, create copies:

```
process-watchdog-app1.applescript
process-watchdog-app2.applescript
```

Each with different `targetApplication` settings.

### Custom Idle Detection

Mac OS 9 idle detection is limited. For better detection:

**Option A**: Use third-party OSAX (scripting addition)
**Option B**: Monitor specific application activity
**Option C**: Use screen saver as proxy (current method)

### Logging to File

To enable file logging:

```applescript
property logFilePath : "Macintosh HD:Monitoring:Logs:heartbeat.log"
```

Create the Logs folder first.

### Custom API Endpoints

To add custom monitoring endpoints:

1. Add endpoint to host controller API
2. Call via `httpLib's sendRequest("/api/v1/your-endpoint")`
3. Example: Custom button, sensor, or status check

---

## Comparison to Windows XP Scripts

| Feature | Windows XP (AutoIt) | Mac OS 9 (AppleScript) |
|---------|---------------------|------------------------|
| HTTP Method | WinHTTP / InetRead | URL Access Scripting / curl |
| Heartbeat Interval | 1 second | 1 second |
| Idle Detection | _Timer_GetIdleTime() | Screen saver activation |
| Process Monitoring | ProcessExists() | System Events process check |
| Window Management | WinSetState() | System Events bounds |
| Compilation | .exe (AutoIt compiler) | .app (Script Editor) |
| Auto-Start | Startup folder | Startup Items |

**Key Differences**:
- Mac OS 9 uses AppleScript vs. AutoIt
- Idle detection more limited on Mac OS 9
- URL Access Scripting instead of WinHTTP
- System Events for process management

**Same Architecture**:
- Modular script design
- 1-second heartbeat interval
- Fail-fast error handling
- Supervisor pattern (heartbeat checks others)
- Snapshot-based recovery

---

## Contributing

Improvements welcome! Areas of interest:

- Better idle time detection methods
- Enhanced window management
- Support for additional Mac OS versions
- Custom scripting additions (OSAX)
- Additional monitoring features
- Documentation improvements

---

## License

MIT License - See LICENSE file in repository root

---

## Support

- **Documentation**: https://github.com/zkmkarlsruhe/exhibition-vm-controller
- **Issues**: https://github.com/zkmkarlsruhe/exhibition-vm-controller/issues
- **Discussions**: GitHub Discussions

---

## Acknowledgments

Developed at ZKM | Center for Art and Media Karlsruhe for the exhibition "Choose Your Filter! Browser Art since the Beginnings of the World Wide Web".

Based on the proven Windows XP AutoIt monitoring architecture, adapted for Mac OS 9 environments.
