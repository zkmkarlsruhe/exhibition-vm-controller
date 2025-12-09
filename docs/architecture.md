# Architecture

## Overview

The Exhibition VM Controller is designed as a multi-layered system that separates concerns between host-level orchestration, guest-level monitoring, and network-level service replacement. This architecture enables robust, autonomous operation of historical digital artworks in exhibition environments.

## System Layers

### Physical Host Layer

The physical host runs a minimal Linux system (tested on Ubuntu 22.04, Debian 12) with:

- **QEMU/KVM**: Hardware-accelerated virtualization
- **libvirt**: VM lifecycle management
- **Openbox**: Lightweight window manager for kiosk mode
- **virt-viewer**: Full-screen VM display with `--kiosk` option
- **Python Host Controller**: Monitoring and orchestration service

The host is dedicated to a single artwork, ensuring fault isolation and simplified configuration.

### Virtualization Layer

Each artwork runs in an isolated virtual machine containing:

- **Guest OS**: Historical operating system (Windows XP, Mac OS 9, etc.)
- **QEMU Guest Agent**: Low-level VM health monitoring
- **Artwork Application**: The digital artwork itself
- **Dependencies**: All required plugins, runtimes, and libraries
- **Monitoring Scripts**: Guest-side watchdog modules

The VM is configured to:
- Start automatically on host boot
- Use bridged or NAT networking for host communication
- Enable QEMU guest agent channel
- Support snapshot operations

### Guest Monitoring Layer

Inside each VM, modular monitoring scripts perform specific tasks:

1. **Heartbeat Module**: Sends periodic "alive" signals to host
2. **Process Watchdog**: Monitors critical application processes
3. **Idle Detection**: Tracks user inactivity
4. **Window Management**: Closes unwanted dialogs and windows
5. **Custom Checks**: Artwork-specific error detection

These modules are implemented in the guest's native scripting language:
- **Windows**: AutoIt scripts
- **Mac OS**: AppleScript (future work)
- **Linux guests**: Shell scripts (future work)

### Host Controller Layer

A Python FastAPI service on the host provides:

- **REST API**: Control interface for VM management
- **Heartbeat Monitor**: Tracks guest health signals
- **Snapshot Manager**: Creates, lists, and reverts snapshots
- **Revert Coordinator**: Triggers automatic recovery
- **Logging**: Operational history and diagnostics

The controller operates as a state machine:
- Receives signals from guest
- Evaluates health status
- Triggers snapshot revert when needed
- Logs all decisions and actions

### Network Service Layer

For artworks with external dependencies, the host provides:

- **HTTP Proxies**: Intercept and redirect artwork requests
- **Mock APIs**: Replicate historical service interfaces
- **Local Mirrors**: Serve archived web content
- **DNS Overrides**: Redirect domains to local services

This layer is artwork-specific and configured per-installation.

## Communication Flow

### Normal Operation

```
┌─────────────────────────────────────────┐
│           Guest VM                      │
│  ┌───────────────────────────────────┐ │
│  │  Artwork Application              │ │
│  └───────────────────────────────────┘ │
│             ↓                           │
│  ┌───────────────────────────────────┐ │
│  │  Monitoring Scripts               │ │
│  │  • Heartbeat (every 1s)           │ │
│  │  • Process check                  │ │
│  │  • Idle detection                 │ │
│  └───────────────────────────────────┘ │
│             ↓ HTTP                      │
└─────────────┼───────────────────────────┘
              ↓
┌─────────────▼───────────────────────────┐
│           Host Controller                │
│  • Update last heartbeat timestamp      │
│  • Monitor QEMU guest agent             │
│  • Check timeout (10s)                  │
│  • Log status                           │
└─────────────────────────────────────────┘
```

### Error Recovery Flow

```
┌─────────────────────────────────────────┐
│         Error Detected                  │
│  • Heartbeat timeout (10s)              │
│  • Process crash                        │
│  • Idle timeout (12 min)                │
│  • Custom error signal                  │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────▼───────────────────────────┐
│      Host Controller Decision            │
│  • Check auto-revert enabled            │
│  • Verify snapshot exists               │
│  • Log error condition                  │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────▼───────────────────────────┐
│      Snapshot Revert                    │
│  • Call virsh snapshot-revert           │
│  • Restore VM to "ready" state          │
│  • Duration: 2-5 seconds                │
│  • Reset heartbeat timer                │
└─────────────┬───────────────────────────┘
              ↓
┌─────────────▼───────────────────────────┐
│      VM Restored                        │
│  • Artwork back in known-good state     │
│  • Monitoring scripts resume            │
│  • Ready for next interaction           │
└─────────────────────────────────────────┘
```

## Design Decisions

### Why One VM Per Physical Host?

**Isolation**: Hardware failures, kernel panics, or virtualization issues affect only one artwork.

**Simplicity**: No routing complexity, no shared resources, no cascading failures.

**Reliability**: Museums prioritize predictability over hardware efficiency.

**Debugging**: Issues are immediately localized to a single system.

### Why Snapshot-Based Recovery?

**Determinism**: Always return to a known-good state, never accumulate errors.

**Speed**: 2-5 second recovery vs. 30-60 second full reboot.

**Simplicity**: Single recovery mechanism for all error types.

**Conservation**: Preserves exact historical configuration without modifications.

### Why Guest-Side Monitoring?

**Visibility**: Host cannot see inside VM processes, windows, or UI state.

**Flexibility**: Each artwork can have custom monitoring logic.

**Maintainability**: Scripts are simple, artwork-specific, and easy to debug.

**No Intrusion**: Works with unmodified artwork code.

### Why HTTP for Communication?

**Universality**: Every legacy OS can make HTTP requests.

**Debugging**: Easy to inspect with standard tools (curl, browser, Wireshark).

**Simplicity**: No complex protocols, authentication, or dependencies.

**Compatibility**: Works across different guest OS versions and network stacks.

### Why Modular Scripts?

**Separation of Concerns**: Each script does one thing well.

**Reusability**: Heartbeat module works across multiple artworks.

**Maintainability**: Easy to understand, test, and modify.

**Debugging**: Failures are isolated to specific modules.

## Scalability Considerations

While the system uses one physical host per artwork, the architecture scales in other dimensions:

**Reusable VM Images**: A single Windows XP base configuration serves multiple artworks.

**Shared Monitoring Logic**: Core watchdog modules are reused with minor tweaks.

**Standardized Patterns**: New artworks follow established setup procedures.

**Documented Workflows**: Reducing time from restoration to exhibition-ready.

For a 20-artwork exhibition, this means 20 physical hosts but shared VM templates, scripts, and operational knowledge.

## Failure Modes and Handling

| Failure Type | Detection Method | Recovery Action |
|--------------|------------------|-----------------|
| Guest OS freeze | Heartbeat timeout | Snapshot revert |
| Application crash | Process watchdog | Snapshot revert |
| Generative deadlock | Idle timeout | Snapshot revert |
| Unwanted windows | Window management script | Auto-close |
| Host crash | Systemd auto-restart | VM auto-start |
| Power loss | BIOS/boot config | Auto-boot host |
| Network failure | Manual intervention | Requires physical access |
| Hardware failure | Manual intervention | Replace host |

## Security Considerations

**Isolation**: VMs are isolated from external networks unless explicitly configured.

**No Remote Access**: Systems operate autonomously without SSH/remote desktop by default.

**Minimal Attack Surface**: Host runs minimal services, guest is historical and offline.

**Physical Security**: Systems are in controlled museum environment.

**Update Policy**: Host receives security updates; guest remains frozen in historical state.

## Performance Characteristics

**Boot Time**:
- Host: 30-60 seconds to Linux login
- VM: 15-30 seconds to Windows XP desktop
- Total cold start: ~90 seconds

**Recovery Time**:
- Snapshot revert: 2-5 seconds
- Process restart: negligible
- User experience interruption: ~5 seconds

**Resource Usage**:
- Host: Minimal (1GB RAM, basic CPU)
- VM: 2-4GB RAM, 1-2 CPU cores
- Disk: 20-50GB per VM (OS + artwork + snapshots)

## Technology Stack

**Host**:
- OS: Ubuntu 22.04 LTS / Debian 12
- Virtualization: QEMU 6.2+ with KVM
- VM Management: libvirt 8.0+
- Language: Python 3.10+
- Framework: FastAPI
- Process Manager: systemd
- Display: Openbox + virt-viewer

**Guest**:
- OS: Windows XP SP3, Mac OS 9 (future), etc.
- Monitoring: QEMU Guest Agent
- Scripting: AutoIt 3.x (Windows), AppleScript (Mac)
- Network: Bridged or NAT

**Network Services**:
- Proxy: nginx or Python http.server
- Mock APIs: Flask/FastAPI
- Archives: wget mirrors, Webrecorder captures

## Future Enhancements

Potential improvements identified but not yet implemented:

- **Automated Testing**: VM snapshot comparison, screenshot regression tests
- **Centralized Logging**: Aggregate logs from multiple hosts for exhibition-wide monitoring
- **Metrics Dashboard**: Real-time visualization of system health
- **Remote Monitoring**: Read-only status API for technicians (without control capabilities)
- **Additional Guest OS**: Mac OS 9, older Windows versions, Linux guests
- **Alternative Hypervisors**: VMware, VirtualBox support
- **Better Error Classification**: Distinguish between temporary glitches and persistent failures

## References

For implementation details, see:
- [Getting Started Guide](getting-started.md) - Step-by-step setup
- [Heartbeat Protocol](heartbeat-protocol.md) - Communication specification
- [API Reference](api-reference.md) - REST endpoints
- [Troubleshooting](troubleshooting.md) - Common issues
