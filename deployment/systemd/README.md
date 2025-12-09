# Systemd Service Configuration

This directory contains systemd service files for running the Exhibition VM Controller as a system service.

## Overview

Running as a systemd service provides:
- **Automatic startup** on boot
- **Automatic restart** on crashes
- **Process management** via systemctl
- **Logging** via journalctl
- **Resource limits** and security hardening

## Installation

### 1. Edit the Service File

Edit `exhibition-vm-controller.service` and change:

```ini
# Working directory
WorkingDirectory=/path/to/exhibition-vm-controller/host-controller

# User/group
User=YOUR_USERNAME
Group=YOUR_USERNAME

# Python executable path (choose one method)
ExecStart=/path/to/.local/bin/poetry run python -m vm_controller.api

# Environment variables
Environment="VMCTL_VM_NAME=your-vm-name"
```

### 2. Copy to Systemd Directory

```bash
sudo cp exhibition-vm-controller.service /etc/systemd/system/
sudo chmod 644 /etc/systemd/system/exhibition-vm-controller.service
```

### 3. Reload Systemd

```bash
sudo systemctl daemon-reload
```

### 4. Enable Service (Start on Boot)

```bash
sudo systemctl enable exhibition-vm-controller
```

### 5. Start Service

```bash
sudo systemctl start exhibition-vm-controller
```

## Service Management

### Check Status

```bash
sudo systemctl status exhibition-vm-controller
```

### View Logs

```bash
# View all logs
sudo journalctl -u exhibition-vm-controller

# Follow logs in real-time
sudo journalctl -u exhibition-vm-controller -f

# View recent logs
sudo journalctl -u exhibition-vm-controller -n 100

# View logs since boot
sudo journalctl -u exhibition-vm-controller -b
```

### Restart Service

```bash
sudo systemctl restart exhibition-vm-controller
```

### Stop Service

```bash
sudo systemctl stop exhibition-vm-controller
```

### Disable Service (Don't Start on Boot)

```bash
sudo systemctl disable exhibition-vm-controller
```

## Configuration Methods

### Method 1: Using Poetry (Recommended for Development)

```ini
ExecStart=/home/username/.local/bin/poetry run python -m vm_controller.api
```

Advantages:
- Isolated dependencies
- Easy updates
- Development and production parity

### Method 2: Using Virtual Environment

```ini
ExecStart=/path/to/exhibition-vm-controller/host-controller/venv/bin/python -m vm_controller.api
```

First create the venv:
```bash
cd /path/to/exhibition-vm-controller/host-controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt  # or use poetry export
```

### Method 3: System Python

```ini
ExecStart=/usr/bin/python3 -m vm_controller.api
```

Requires system-wide installation:
```bash
sudo pip3 install -e /path/to/exhibition-vm-controller/host-controller
```

## Environment Variables

Configure via `Environment=` directives in the service file:

```ini
Environment="VMCTL_VM_NAME=my-artwork-vm"
Environment="VMCTL_SNAPSHOT_NAME=ready"
Environment="VMCTL_API_PORT=8000"
Environment="VMCTL_HEARTBEAT_TIMEOUT=15.0"
Environment="VMCTL_AUTO_REVERT_ENABLED=true"
Environment="VMCTL_LOG_LEVEL=INFO"
```

Or use an environment file:

```ini
EnvironmentFile=/path/to/exhibition-vm-controller/host-controller/.env
```

## Security Considerations

### User Permissions

The service user needs:
- **libvirt access**: Add user to `libvirt` group
  ```bash
  sudo usermod -a -G libvirt YOUR_USERNAME
  ```
- **Network access**: For API endpoints
- **Read/write access**: To working directory

### Systemd Security Features

Uncomment these in the service file for enhanced security:

```ini
[Service]
# Prevent privilege escalation
NoNewPrivileges=true

# Use private /tmp
PrivateTmp=true

# Protect system directories
ProtectSystem=strict
ProtectHome=true

# Allow writing only to specific paths
ReadWritePaths=/path/to/exhibition-vm-controller
```

**Note**: These may need adjustment based on your setup and libvirt configuration.

## Troubleshooting

### Service Fails to Start

1. **Check service status**:
   ```bash
   sudo systemctl status exhibition-vm-controller
   ```

2. **View detailed logs**:
   ```bash
   sudo journalctl -u exhibition-vm-controller -n 50 --no-pager
   ```

3. **Common issues**:
   - Wrong paths in `WorkingDirectory` or `ExecStart`
   - Python not found
   - Missing dependencies
   - Permission errors (libvirt access, file permissions)
   - Config file not found or invalid

### Service Restarts Repeatedly

Check logs for errors:
```bash
sudo journalctl -u exhibition-vm-controller -f
```

Common causes:
- Invalid configuration (check `config.yaml`)
- VM not found in libvirt
- Snapshot doesn't exist
- Network configuration issues

### Libvirt Permission Denied

```bash
# Add user to libvirt group
sudo usermod -a -G libvirt YOUR_USERNAME

# Re-login or restart service
sudo systemctl restart exhibition-vm-controller
```

### Can't Connect to API

1. Check service is running:
   ```bash
   sudo systemctl status exhibition-vm-controller
   ```

2. Check port binding:
   ```bash
   sudo ss -tlnp | grep 8000
   ```

3. Test locally:
   ```bash
   curl http://localhost:8000/api/v1/status
   ```

## Integration with Other Services

### Auto-start VM on Boot

The service file includes:
```ini
After=libvirtd.service
Wants=libvirtd.service
```

This ensures libvirtd is running before the controller starts.

### Integration with Display Manager

For kiosk mode with auto-login, coordinate with:
- Display manager (lightdm, gdm, etc.)
- Openbox autostart (see `../openbox/`)
- virt-viewer kiosk mode

## Performance Tuning

### Resource Limits

Add to `[Service]` section:

```ini
# File descriptor limit
LimitNOFILE=65536

# Process limit
LimitNPROC=4096

# Memory limit (soft, hard) - 1GB example
MemoryLimit=1G
```

### CPU and I/O Priority

```ini
# Lower CPU priority (nice value)
Nice=10

# I/O priority (best-effort, priority 4)
IOSchedulingClass=best-effort
IOSchedulingPriority=4
```

## Author

Marc Schütze (mschuetze@zkm.de)
ZKM | Center for Art and Media Karlsruhe

## License

MIT License - See repository LICENSE file
