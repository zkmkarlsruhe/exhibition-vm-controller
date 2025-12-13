# Exhibition VM Controller - Host Controller

Python FastAPI application for controlling virtual machines in exhibition environments.

## Overview

This is the host controller component that runs on the physical Linux host machine. It provides:
- REST API for VM control and monitoring
- Heartbeat monitoring system
- Automatic snapshot-based recovery
- Configuration management

## Installation

### Prerequisites

- Python 3.10 or higher
- Poetry for dependency management
- libvirt-clients (provides `virsh` command)
- QEMU/KVM with an active VM configured with QEMU guest agent

### System Dependencies

Install libvirt tools:

```bash
# Ubuntu/Debian
sudo apt-get install libvirt-clients

# Fedora/RHEL
sudo dnf install libvirt-client

# Arch Linux
sudo pacman -S libvirt
```

### Using Poetry (Recommended)

```bash
# Install Poetry if not already installed
curl -sSL https://install.python.org/poetry.py | python3 -

# Install dependencies
poetry install --no-root

# Run the controller
poetry run python -m vm_controller.api
```

### Using pip (Alternative)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install from pyproject.toml
pip install -e .

# Run the controller
python -m vm_controller.api
```

## Configuration

### Create Configuration File

```bash
# Copy example config
cp examples/config.example.yaml config.yaml

# Edit configuration
nano config.yaml
```

### Minimum Configuration

```yaml
vm_name: "your-vm-name"  # Required
snapshot_name: "ready"
api_port: 8000
```

### Environment Variables

Override any configuration with environment variables:

```bash
export VMCTL_VM_NAME="my-vm"
export VMCTL_API_PORT=9000
poetry run python -m vm_controller.api
```

## Usage

### Start API Server

```bash
# Development
poetry run python -m vm_controller.api

# Production (with systemd)
sudo systemctl start exhibition-vm-controller
```

### API Endpoints

Once running, access the API:

```bash
# Check status
curl http://localhost:8000/api/v1/status

# Receive heartbeat (called by guest scripts)
curl -X POST http://localhost:8000/api/v1/heartbeat

# Restart VM
curl -X POST http://localhost:8000/api/v1/vm/restart

# Create snapshot
curl -X POST http://localhost:8000/api/v1/snapshot/create

# Enable/disable auto-revert
curl -X POST http://localhost:8000/api/v1/revert/enable
curl -X POST http://localhost:8000/api/v1/revert/disable
```

### Interactive API Documentation

FastAPI provides automatic interactive documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Development

### Run in Development Mode

```bash
# Enable auto-reload
poetry run uvicorn vm_controller.api:app --reload --port 8000
```

### Run Tests

```bash
poetry run pytest
```

### Code Quality

```bash
# Format code
poetry run black vm_controller/

# Lint
poetry run ruff vm_controller/

# Type checking
poetry run mypy vm_controller/
```

## Project Structure

```
host-controller/
├── pyproject.toml           # Dependencies and project metadata
├── config.yaml              # Configuration (create from example)
├── vm_controller/           # Main package
│   ├── __init__.py          # Package initialization
│   ├── vm_manager.py        # VM control via libvirt
│   ├── heartbeat_monitor.py # Heartbeat tracking
│   ├── api.py               # FastAPI REST API
│   └── config.py            # Configuration management
├── examples/                # Usage examples
│   ├── config.example.yaml  # Configuration template
│   └── basic_usage.py       # Python API usage example
└── tests/                   # Unit tests (to be added)
```

## Python API Usage

You can use the VM controller programmatically:

```python
from vm_controller import VMManager, HeartbeatMonitor, Config

# Load configuration
config = Config.from_yaml("config.yaml")

# Create VM manager
vm_manager = VMManager(
    vm_name=config.vm_name,
    snapshot_name=config.snapshot_name
)

# Control VM
vm_manager.start_vm()
vm_manager.create_snapshot("backup")
vm_manager.restart_vm()

# Check status
is_running = vm_manager.is_running()
is_responsive = vm_manager.check_vm_responsiveness()
```

## Permissions

The user running the controller needs:

```bash
# Add user to libvirt group
sudo usermod -a -G libvirt $USER

# Re-login or use newgrp
newgrp libvirt

# Verify access
virsh list --all
```

## Troubleshooting

### "Permission denied" when accessing libvirt

```bash
# Check groups
groups

# Should include 'libvirt'
# If not, add user and re-login
sudo usermod -a -G libvirt $USER
```

### "VM not found"

```bash
# List all VMs
virsh list --all

# Check VM name matches config
cat config.yaml | grep vm_name
```

### "Snapshot does not exist"

```bash
# List snapshots
virsh snapshot-list your-vm-name

# Create ready snapshot
curl -X POST http://localhost:8000/api/v1/snapshot/create
```

### QEMU Guest Agent Not Responding

```bash
# Check guest agent in VM
virsh qemu-agent-command your-vm-name '{"execute":"guest-ping"}'

# Should return: {"return":{}}
# If error, install qemu-guest-agent in VM
```

## Deployment

See `../deployment/` directory for:
- **systemd**: Run as system service
- **openbox**: Display configuration
- **nginx**: Reverse proxy (optional)

## Contributing

Contributions welcome! Please:
- Follow existing code style (black, ruff)
- Add tests for new features
- Update documentation

## Author

Marc Schütze (mschuetze@zkm.de)
ZKM | Center for Art and Media Karlsruhe

## License

MIT License - See LICENSE file in repository root
