# Exhibition VM Controller

A framework for the conservation and autonomous exhibition operation of historical digital artworks using virtual machines with snapshot-based error recovery.

## Overview

This project provides a robust system for running legacy digital artworks in museum and exhibition contexts. It addresses the challenge of **technical obsolescence** by preserving entire historical computing environments as virtual machines, with automated error detection and recovery.

Developed at **ZKM | Center for Art and Media Karlsruhe** for the conservation of digital artworks that depend on obsolete operating systems, browser plugins, or multimedia engines (Flash, Pulse 3D, early Java-QuickTime integrations, etc.).

### Core Concept

The system combines three technical approaches:

1. **Virtual Machines (VMs)** - Preserve historical operating systems and their dependencies
2. **Snapshots** - Create restorable system states ("ready" snapshots)
3. **Automated Monitoring** - Detect failures and automatically revert to known-good states

When errors occur (crashes, frozen windows, timeout), the system automatically reverts the VM to its "ready" snapshot within seconds, ensuring continuous operation without human intervention.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Physical Host (Linux)                 │
│  ┌────────────────────────────────────────────────────┐ │
│  │   Openbox (Window Manager)                         │ │
│  │   └── virt-viewer --kiosk (VM Display)             │ │
│  └────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────┐ │
│  │      Python Host Controller (FastAPI)              │ │
│  │  • Receives heartbeat signals                      │ │
│  │  • Monitors VM health via QEMU guest agent         │ │
│  │  • Controls VM lifecycle (start/stop/snapshot)     │ │
│  │  • REST API for control and management             │ │
│  └────────────────┬───────────────────────────────────┘ │
│                   │ HTTP/Network + QEMU Guest Agent     │
│  ┌────────────────▼───────────────────────────────────┐ │
│  │           Virtual Machine (QEMU/KVM)               │ │
│  │  ┌──────────────────────────────────────────────┐ │ │
│  │  │  Guest OS (Windows XP, Mac OS 9, etc.)       │ │ │
│  │  │  • QEMU Guest Agent (qemu-ga)                │ │ │
│  │  │  • Monitoring scripts (AutoIT, AppleScript)  │ │ │
│  │  │  • Artwork application                        │ │ │
│  │  │  • Sends heartbeat every 1s                   │ │ │
│  │  │  • Reports errors/idle states                 │ │ │
│  │  └──────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Display System

For exhibition presentation:
- **Openbox**: Lightweight window manager for the host system
- **virt-viewer --kiosk**: Displays the VM in full-screen kiosk mode, hiding host UI
- **Autostart**: virt-viewer launches automatically on boot for unattended operation

For VM management and configuration:
- **virt-manager**: GUI tool for creating, modifying, and managing VMs

### Communication & Monitoring

**QEMU Guest Agent**:
- Must be installed in every guest VM
- Enables host to verify VM responsiveness at hypervisor level
- Used for `guest-ping` health checks independent of network

**Application-Level Heartbeat**:
- Guest scripts send HTTP heartbeat signals
- Proves guest OS and applications are functioning
- Complements QEMU agent checks

**Communication Protocol**:
- **Default**: HTTP requests from guest to host
- **Alternative**: Any network-based signaling supported by the legacy OS
- **Heartbeat**: Periodic "alive" signal (default: every 1 second)
- **Timeout**: Host considers guest unresponsive after 10 seconds
- **Error Signals**: Immediate notifications (process crash, idle timeout, application-specific errors)

## Project Structure

```
exhibition-vm-controller/
├── README.md                          # This file
├── LICENSE                            # MIT License
├── docs/                              # Detailed documentation
│   ├── architecture.md                # System architecture
│   ├── getting-started.md             # Setup guide
│   ├── heartbeat-protocol.md          # Communication protocol
│   ├── api-reference.md               # REST API documentation
│   └── troubleshooting.md             # Common issues
├── guest-scripts/                     # VM guest monitoring scripts
│   └── windows-xp/                    # Windows XP examples (AutoIT)
│       ├── README.md                  # Setup guide
│       ├── heartbeat.au3              # Heartbeat sender
│       ├── idle-monitor.au3           # Idle detection
│       ├── process-watchdog.au3       # Process monitoring
│       └── run.au3                    # Process launcher
├── host-controller/                   # Python host controller (Poetry project)
│   ├── pyproject.toml                 # Dependencies
│   ├── README.md                      # Installation guide
│   ├── vm_controller/                 # Core library
│   │   ├── __init__.py
│   │   ├── vm_manager.py              # VM control (libvirt/virsh)
│   │   ├── heartbeat_monitor.py       # Heartbeat tracking
│   │   ├── api.py                     # REST API (FastAPI)
│   │   └── config.py                  # Configuration
│   └── examples/                      # Usage examples
│       ├── config.example.yaml        # Configuration template
│       └── basic_usage.py             # Python API example
└── deployment/                        # Deployment templates
    ├── systemd/                       # Systemd service templates
    │   ├── exhibition-vm-controller.service
    │   └── README.md
    ├── openbox/                       # Openbox configuration
    │   ├── autostart                  # Autostart virt-viewer
    │   └── README.md
    └── nginx/                         # Nginx reverse proxy examples
        └── README.md
```

## Key Features

### Automated Error Recovery

- **QEMU Guest Agent Monitoring**: Low-level VM responsiveness checks
- **Heartbeat Monitoring**: Application-level health verification
- **Idle Detection**: Automatic reset after configurable inactivity period
- **Process Monitoring**: Verifies critical applications are running
- **Instant Recovery**: Revert to snapshot in 2-5 seconds

### Modular Guest Scripts

Specialized scripts handle specific monitoring tasks:
- **Heartbeat**: Proves system and application responsiveness
- **Idle Timer**: Detects user inactivity
- **Process Watchdog**: Monitors application lifecycle
- **Custom Checks**: Application-specific error detection

### REST API

The host controller provides a REST API for:
- **VM Control**: Start, stop, restart VMs
- **Snapshot Management**: Create, delete, revert snapshots
- **Heartbeat Monitoring**: View status and configure timeouts
- **Revert System**: Enable/disable automatic revert functionality
- **Health Checks**: Monitor system and VM health

See `docs/api-reference.md` for complete API documentation.

### Display Management

- **Kiosk Mode**: Full-screen presentation using virt-viewer --kiosk
- **Autostart**: Automatic launch on boot for unattended operation
- **Openbox Integration**: Lightweight, reliable window management

## Quick Start

### Prerequisites

**Host System**:
- Linux (tested on Ubuntu 22.04, Debian 12)
- KVM/QEMU and libvirt installed
- Python 3.10+
- Poetry for Python dependency management
- Openbox window manager
- virt-viewer for VM display

**Guest System**:
- **QEMU Guest Agent** installed and running (critical!)
- Ability to run monitoring scripts
- Network connectivity to host

### Installation

```bash
# Clone repository
git clone https://github.com/zkmkarlsruhe/exhibition-vm-controller
cd exhibition-vm-controller/host-controller

# Install dependencies
poetry install

# Copy and configure
cp examples/config.example.yaml config.yaml
# Edit config.yaml with your VM name and settings

# Run controller
poetry run python -m vm_controller.api
```

### VM Setup

1. **Create VM using virt-manager**:
   ```bash
   virt-manager
   ```
   - Create VM with desired legacy OS
   - Install OS and artwork application
   - Configure networking (bridged or NAT)

2. **Install QEMU Guest Agent** (REQUIRED):
   - **Windows**: Install `qemu-ga` from VirtIO drivers ISO
   - **Linux**: `apt install qemu-guest-agent` or equivalent
   - **Mac OS**: May require manual compilation or alternatives

3. **Install Guest Scripts**:
   - Copy scripts from `guest-scripts/windows-xp/` to VM
   - Configure host URL in scripts
   - Set scripts to run at startup

4. **Test and Create Snapshot**:
   ```bash
   # Verify QEMU guest agent is working
   virsh qemu-agent-command YOUR_VM_NAME '{"execute":"guest-ping"}'

   # Test heartbeat is working
   curl http://localhost:8000/api/v1/status

   # Create ready snapshot once everything works
   curl -X POST http://localhost:8000/api/v1/snapshot/create
   ```

5. **Configure Display**:
   - Copy openbox autostart script
   - Configure virt-viewer to launch in kiosk mode
   - Test presentation mode

See `docs/getting-started.md` for detailed setup instructions.

## REST API Examples

### Check VM Status
```bash
curl http://localhost:8000/api/v1/status
```

### Enable/Disable Auto-Revert
```bash
# Disable automatic revert (for maintenance)
curl -X POST http://localhost:8000/api/v1/revert/disable

# Enable automatic revert
curl -X POST http://localhost:8000/api/v1/revert/enable
```

### Manage Snapshots
```bash
# Create new snapshot
curl -X POST http://localhost:8000/api/v1/snapshot/create

# Delete snapshot
curl -X DELETE http://localhost:8000/api/v1/snapshot/ready

# Manual revert
curl -X POST http://localhost:8000/api/v1/snapshot/revert
```

## Use Cases

This framework was developed for and tested with:
- **Digital Art Conservation**: Preserve interactive artworks dependent on obsolete technology
- **Exhibition Operation**: Unattended operation over weeks/months
- **Historical Software Preservation**: Run legacy systems reliably on modern hardware

### Tested With

- Windows XP (Internet Explorer 6, Flash, custom plugins)
- Legacy multimedia engines (Pulse 3D, early Java-QuickTime)
- Browser-based artworks requiring specific rendering engines

## Technical Requirements

### Host System

- **OS**: Linux (tested on Ubuntu 22.04, Debian 12)
- **Virtualization**: libvirt + QEMU/KVM
- **Python**: 3.10+
- **Window Manager**: Openbox (for presentation mode)
- **VM Display**: virt-viewer with --kiosk option
- **Network**: Internal network between host and guest VMs

### Guest System

- **QEMU Guest Agent**: Required for VM health monitoring
- **Monitoring Scripts**: AutoIT (Windows), AppleScript (Mac), or shell scripts
- **Network**: Ability to send HTTP requests to host
- **Snapshot Support**: Guest OS must be compatible with libvirt snapshots

## Configuration Parameters

Key configurable values (all adjustable in `config.yaml`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `vm_name` | - | Name of the VM in libvirt |
| `snapshot_name` | "ready" | Name of the reference snapshot |
| `heartbeat_interval` | 1s | How often guest sends heartbeat |
| `heartbeat_timeout` | 10s | When host considers guest dead |
| `idle_timeout` | 12-15min | Inactivity before auto-reset |
| `auto_revert_enabled` | true | Enable automatic revert on failure |
| `api_port` | 8000 | REST API port |
| `check_qemu_agent` | true | Verify VM via QEMU guest agent |

## Documentation

- **[Getting Started](docs/getting-started.md)**: Complete setup guide
- **[Architecture](docs/architecture.md)**: Technical design and decisions
- **[Heartbeat Protocol](docs/heartbeat-protocol.md)**: Communication specification
- **[API Reference](docs/api-reference.md)**: REST API endpoints
- **[Troubleshooting](docs/troubleshooting.md)**: Common issues and solutions

## Real-World Testing

This system has been tested in production exhibition environments:

- **Duration**: 6 months continuous operation
- **Scale**: 12 VMs running simultaneously
- **Uptime**: No manual intervention required during exhibition hours
- **Reliability**: Automatic recovery from all observed failure modes

## License

MIT License - See [LICENSE](LICENSE) file for details.

**Attribution Required**: When using this project in academic or commercial contexts, please credit:
- **Marc Schütze** (mschuetze@zkm.de)
- **ZKM | Center for Art and Media Karlsruhe**

## Contributing

Contributions are welcome, especially for:
- Additional guest OS examples (Mac OS 9, older Windows versions)
- Improved error detection strategies
- Documentation improvements
- Additional virtualization backends

## Citation

If you use this framework in academic work, please cite:

```bibtex
@software{schuetze2025exhibition,
  author = {Schütze, Marc},
  title = {Exhibition VM Controller: Snapshot-Based Conservation of Digital Artworks},
  year = {2025},
  organization = {ZKM | Center for Art and Media Karlsruhe},
  url = {https://github.com/zkmkarlsruhe/exhibition-vm-controller}
}
```

## Contact

**Marc Schütze**
ZKM | Center for Art and Media Karlsruhe
mschuetze@zkm.de

## Acknowledgments

This software was created within the framework of the exhibition *Choose Your Filter! Browser Art since the Beginnings of the World Wide Web* at ZKM | Center for Art and Media Karlsruhe in cooperation with the Karlsruhe Institute of Technology (KIT).

**Funding**: The German Research Foundation (DFG) and the European Research Council (ERC, under the European Union's Horizon 2020 research and innovation programme under grant agreement COSE, No. 101045376) funded the research projects underlying the exhibition.

**Developed by**: Marc Schütze at ZKM | Center for Art and Media Karlsruhe in collaboration with Daniel Heiss, Matthieu Vlaminck, and Leonie Rök. Technical project management by Matthias Gommel.

**Curated by**: Laura C. Schmidt, Inge Hinterwaldner, and Daniela Hönigsberg.

Special thanks to ZKM for the opportunity to conduct this research and develop the methods described here, and to Rhizome for pioneering work in digital art preservation and emulation-based conservation.
