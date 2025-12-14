# Changelog

All notable changes to the Exhibition VM Controller project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Support for Mac OS 9 guest monitoring scripts (AppleScript-based)
- Support for Linux guest monitoring scripts (shell script-based)
- WebSocket API for real-time status updates
- Centralized logging dashboard for multiple exhibition hosts
- Automated testing framework
- Docker containerization for host controller
- VMware and VirtualBox backend support
- Metrics collection and visualization (Prometheus/Grafana integration)
- Modular plugin system for custom polling and signaling handlers

## [1.3.0] - 2025-12-13

### Added
- **Web UI for remote VM management** - Simple, responsive web interface at `/ui/` for maintenance personnel to control VMs without SSH access
  - System status dashboard showing VM state, snapshot, heartbeat activity, and auto-revert status
  - VM control buttons (Start, Stop)
  - Snapshot management (Create, Revert to Snapshot)
  - Auto-revert toggle (Enable/Disable for maintenance mode)
  - Real-time monitoring with auto-refresh every 5 seconds
  - Clean single-page interface with no authentication (for trusted local networks)
- **Manual stop tracking** - System distinguishes between intentional VM stop via API/UI (stays stopped) vs. guest shutdown or crash (auto-restarts)
- **Improved heartbeat status tracking** - Distinguishes between actual guest heartbeats and internal monitoring timer
  - Shows "Waiting" status when no heartbeat received yet
  - Shows "Healthy" only when actual heartbeat received from guest
  - Detailed heartbeat activity card with time since last heartbeat and health status

### Changed
- **Simplified heartbeat monitoring** - Monitoring is now controlled solely by `auto_revert_enabled` setting, removing redundant toggle
  - Heartbeat monitoring automatically starts/stops with auto-revert enable/disable
  - No more separate enable/disable of heartbeat monitoring
- **Heartbeat timer initialization** - Timer now starts automatically when monitoring begins, enabling timeout detection even before first heartbeat
- **VM state monitoring respects manual stops** - Auto-restart only triggers on unexpected shutdowns, not manual stops via API/UI

### Fixed
- Heartbeat status now correctly reports whether actual heartbeat was received from guest
- Manual VM stop via API/UI no longer triggers immediate auto-restart
- Web UI correctly displays heartbeat status using proper API property names

### Benefits
- Maintenance personnel can control VMs remotely via browser without SSH
- Clear distinction between maintenance mode (manual stop) and failure recovery (auto-restart)
- Accurate heartbeat status prevents false "healthy" reports when no heartbeat received
- Simplified monitoring architecture easier to understand and maintain

## [1.2.0] - 2025-12-13

### Added
- **GET request support for all API endpoints** - All POST endpoints now also accept GET requests for compatibility with AutoIt and other tools that cannot make POST requests
- GET support for: `/api/v1/heartbeat`, `/api/v1/vm/start`, `/api/v1/vm/stop`, `/api/v1/vm/restart`, `/api/v1/snapshot/create`, `/api/v1/snapshot/delete/{name}`, `/api/v1/revert/enable`, `/api/v1/revert/disable`

### Changed
- **AutoIt compatibility** - All control endpoints can now be called with simple GET requests from AutoIt guest scripts
- DELETE endpoint `/api/v1/snapshot/{name}` now also available as GET at `/api/v1/snapshot/delete/{name}`

### Benefits
- Enables simple HTTP control from legacy environments (Windows XP, older AutoIt versions)
- No JSON parsing required for basic operations
- Maintains backward compatibility with existing POST-based clients

## [1.1.0] - 2025-12-13

### Added
- **Automatic VM startup on controller launch** - VM now automatically starts and reverts to "ready" snapshot when controller starts, ensuring clean initial state every time (commit 1551d56)
- **VM state monitoring and auto-recovery** - System continuously monitors VM state (every 0.5s) and automatically restarts if VM is manually shut down or crashes (commit 1551d56)
- Poetry script shortcuts (`vm-controller`, `vmctl`) for easier execution (commit 91e7871)
- Link to virtio-win drivers for Windows guest setup in documentation (commit 91e7871)

### Changed
- **QEMU guest agent is now optional** - System works perfectly without guest agent installed; set `check_qemu_agent: false` in config (commit 91e7871)
- Improved configuration documentation with clearer guidance on QEMU guest agent usage (commit 91e7871)
- HeartbeatMonitor now accepts `vm_manager` parameter for VM state monitoring (commit 1551d56)

### Fixed
- Fixed 5-minute timeout when QEMU agent checking is disabled - now skips wait entirely, reducing revert time from 300s to 4s (commit 91e7871)
- Fixed race condition in heartbeat monitoring enable/disable cycle after timeout callback (commit 91e7871)
- VM restart now properly skips QEMU agent wait when `check_qemu_agent: false` (commit 91e7871)

### Performance
- VM revert time reduced from 300s to 4s when QEMU agent is disabled
- Manual VM shutdown detection: 0.5 seconds
- Auto-recovery from shutdown: ~4 seconds
- Auto-revert cycle: ~29 seconds (15s timeout + 4s revert + 10s delay)

## [1.0.0] - 2025-12-09

### Added
- Initial public release of Exhibition VM Controller
- Core host controller with FastAPI REST API
- VM lifecycle management via libvirt/virsh
- Snapshot-based automatic recovery system
- Heartbeat monitoring with configurable timeouts
- QEMU guest agent integration for low-level health checks
- Windows XP guest monitoring scripts (AutoIt)
  - heartbeat.au3 - Periodic alive signal
  - idle-monitor.au3 - User inactivity detection
  - process-watchdog.au3 - Application monitoring
  - run.au3 - Process launcher
- Comprehensive documentation
  - Architecture overview
  - Getting Started guide
  - Heartbeat Protocol specification
  - API Reference
  - Troubleshooting guide
- Deployment configurations
  - Systemd service templates
  - Openbox autostart configuration
  - Nginx reverse proxy examples
- Academic publication support files
  - CITATION.cff for software citation
  - codemeta.json for software metadata
  - .zenodo.json for DOI registration
  - AUTHORS file with contributor information
  - CONTRIBUTING.md with contribution guidelines
- Configuration management via YAML
- Python Poetry-based dependency management
- MIT License

### Tested
- 6+ months of continuous operation in exhibition environment
- "Choose Your Filter! Browser Art since the Beginnings of the World Wide Web" exhibition at ZKM Karlsruhe (February-August 2025)
- 12 concurrent VM instances across multiple physical hosts
- Windows XP guests with various browser-based artworks
- Successful restoration of 8 historical digital artworks:
  - Eden.Garden (2001) by Entropy8Zuper!
  - Subfusion (2001-2002) by Stanza
  - Wrong Browser Series (2001-2012) by JODI
  - Browser Gestures (2001) by Mark Daggett
  - ZNC browser 2.0 (2003) by Peter Luining
  - <earshot> (1999) by Andy Freeman and Jason Skeets
  - Reconnoitre (1997-2002) by Gavin Baily and Tom Corby

### Performance
- Snapshot revert: 2-5 seconds
- Heartbeat interval: 1 second
- Heartbeat timeout: 10 seconds (configurable)
- Idle timeout: 12 minutes (configurable)
- Recovery time: <10 seconds total (timeout + revert)

### Supported Platforms

**Host**:
- Ubuntu 22.04 LTS (primary test platform)
- Debian 12 (tested)
- Other Linux distributions with KVM/QEMU support (should work)

**Guest**:
- Windows XP SP3 (extensively tested)
- Windows 7 (tested)
- Windows 10 (tested)
- Mac OS 9 (partial support, scripts in development)
- Linux guests (partial support, scripts in development)

## Release Notes

### Version 1.0.0 - Initial Release

This is the first public release of the Exhibition VM Controller, developed at ZKM | Center for Art and Media Karlsruhe for the conservation and exhibition of historical digital artworks.

**Key Features**:
- **Autonomous Operation**: Runs unattended for months with automatic error recovery
- **Snapshot-Based Recovery**: Deterministic restoration to known-good states
- **Modular Architecture**: Clear separation between host orchestration and guest monitoring
- **Exhibition-Tested**: Proven in 6+ months of real-world museum deployment
- **Well Documented**: Comprehensive guides for setup, operation, and troubleshooting
- **Open Source**: MIT licensed, ready for reuse by other institutions

**Use Cases**:
- Digital art conservation and exhibition
- Historical software preservation
- Legacy system emulation
- Interactive museum installations
- Long-term exhibition operation with minimal maintenance

**Academic Context**:
This software is the practical implementation of research conducted for the exhibition "Choose Your Filter! Browser Art since the Beginnings of the World Wide Web" at ZKM Karlsruhe. It demonstrates that virtualization combined with automated monitoring and snapshot-based recovery provides a robust foundation for exhibiting fragile, internet-dependent digital artworks.

**Acknowledgments**:
This software was created within the framework of the exhibition "Choose Your Filter! Browser Art since the Beginnings of the World Wide Web" at ZKM | Center for Art and Media Karlsruhe in cooperation with the Karlsruhe Institute of Technology (KIT).

**Funding**: The German Research Foundation (DFG) and the European Research Council (ERC, under the European Union's Horizon 2020 research and innovation programme under grant agreement COSE, No. 101045376) funded the research projects underlying the exhibition.

**Developed by**: Marc Schütze at ZKM | Center for Art and Media Karlsruhe in collaboration with Daniel Heiss, Matthieu Vlaminck, and Leonie Rök. Technical project management by Matthias Gommel.

**Curated by**: Laura C. Schmidt, Inge Hinterwaldner, and Daniela Hönigsberg.

Special thanks to ZKM for the opportunity to conduct this research and develop the methods described here, and to Rhizome for pioneering work in digital art preservation and emulation-based conservation.

---

## Version History

### Pre-Release Development

**2024-07 to 2025-01**: Development and testing phase
- Architecture design and implementation
- Testing with multiple artworks in parallel
- Refinement based on exhibition operation feedback
- Documentation writing

**2024-02**: Project inception
- Initial concept and requirements gathering
- Selection of virtualization approach
- First prototypes with Windows XP VMs

---

## Migration Guide

### From Pre-Release Versions

If you were using development versions of this software:

1. **Configuration Changes**:
   - Configuration is now YAML-based (`config.yaml`) instead of environment variables
   - Copy `examples/config.example.yaml` and adapt to your setup
   - Old `.env` files are no longer supported

2. **API Changes**:
   - API structure is stable and follows REST conventions
   - Endpoints are now versioned under `/api/v1/`
   - Previous development endpoints may have changed

3. **Dependencies**:
   - Now managed via Poetry
   - Run `poetry install` to set up dependencies
   - Python 3.10+ is required

4. **Guest Scripts**:
   - Scripts are now located in `guest-scripts/windows-xp/`
   - Update host URL in scripts to match your configuration
   - Recompile AutoIt scripts if using old versions

---

## Reporting Issues

Found a bug or have a suggestion?

1. Check existing issues: https://github.com/zkmkarlsruhe/exhibition-vm-controller/issues
2. If new, create an issue with:
   - Clear description
   - Steps to reproduce
   - Expected vs actual behavior
   - Your environment (OS, QEMU version, Python version)
   - Relevant log excerpts

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Areas of particular interest:
- Guest scripts for additional operating systems
- Alternative virtualization backend support
- Improved error detection strategies
- Documentation improvements
- Test coverage

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) file for details.

---

## Citation

If you use this software in academic work, please cite:

```bibtex
@software{schuetze2025exhibition,
  author = {Schütze, Marc},
  title = {Exhibition VM Controller: Snapshot-Based Conservation of Digital Artworks},
  year = {2025},
  version = {1.3.0},
  organization = {ZKM | Center for Art and Media Karlsruhe},
  url = {https://github.com/zkmkarlsruhe/exhibition-vm-controller}
}
```

See [CITATION.cff](CITATION.cff) for machine-readable citation metadata.

---

*Last updated: 2025-12-14*
