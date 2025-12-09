# Getting Started

This guide walks you through setting up the Exhibition VM Controller from scratch, including host preparation, VM creation, guest script installation, and display configuration.

## Prerequisites

### Hardware Requirements

- **CPU**: Intel/AMD with VT-x/AMD-V virtualization support
- **RAM**: 8GB minimum (16GB recommended)
- **Disk**: 100GB free space per artwork
- **Display**: HDMI/DisplayPort output for exhibition display
- **Network**: Ethernet connection (recommended for stability)

### Software Requirements

**Host Operating System**:
- Ubuntu 22.04 LTS or Debian 12 (recommended)
- Other Linux distributions may work but are untested

**Installation packages**:
```bash
sudo apt update
sudo apt install -y \
    qemu-kvm \
    libvirt-daemon-system \
    libvirt-clients \
    bridge-utils \
    virt-manager \
    virt-viewer \
    python3.10 \
    python3-pip \
    openbox \
    git
```

**Python dependency manager**:
```bash
curl -sSL https://install.python-poetry.org | python3 -
```

## Step 1: Host System Setup

### 1.1 Verify Virtualization Support

Check if your CPU supports hardware virtualization:

```bash
egrep -c '(vmx|svm)' /proc/cpuinfo
```

If the output is greater than 0, virtualization is supported.

Verify KVM is loaded:

```bash
lsmod | grep kvm
```

You should see `kvm_intel` or `kvm_amd`.

### 1.2 Configure User Permissions

Add your user to the necessary groups:

```bash
sudo usermod -a -G libvirt $USER
sudo usermod -a -G kvm $USER
```

Log out and log back in for changes to take effect.

### 1.3 Start libvirt Service

```bash
sudo systemctl enable libvirtd
sudo systemctl start libvirtd
sudo systemctl status libvirtd
```

### 1.4 Clone the Repository

```bash
cd ~
git clone https://github.com/zkmkarlsruhe/exhibition-vm-controller
cd exhibition-vm-controller
```

## Step 2: Create and Configure the VM

### 2.1 Launch virt-manager

```bash
virt-manager
```

### 2.2 Create a New VM

1. Click **File → New Virtual Machine**
2. Choose **Local install media (ISO image or CDROM)**
3. Click **Forward**

### 2.3 Select Installation Media

1. Click **Browse** → **Browse Local**
2. Select your OS ISO (e.g., Windows XP SP3)
3. Set **OS type** and **Version** appropriately
4. Click **Forward**

### 2.4 Configure Memory and CPU

Recommended settings for Windows XP:
- **Memory**: 2048 MB (2GB)
- **CPUs**: 2 cores

Click **Forward**.

### 2.5 Configure Storage

- **Create a disk image**: 40 GB minimum
- **Allocate entire disk now**: Unchecked (faster creation)

Click **Forward**.

### 2.6 Name the VM

- **Name**: Something descriptive (e.g., `eden-garden-vm`)
- **Network selection**: NAT or Bridge (NAT recommended)
- Check **Customize configuration before install**
- Click **Finish**

### 2.7 Add QEMU Guest Agent Channel

Before starting installation:

1. In the configuration window, click **Add Hardware**
2. Select **Channel**
3. Set **Name**: `org.qemu.guest_agent.0`
4. Set **Device Type**: `unix`
5. Click **Finish**

This is **critical** for host-level VM monitoring.

### 2.8 Install Guest OS

1. Click **Begin Installation**
2. Install your operating system normally
3. Complete all Windows updates if applicable
4. Install drivers, plugins, artwork application

## Step 3: Install QEMU Guest Agent in VM

### For Windows XP/7/10

1. Download VirtIO drivers ISO from: https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/
2. In virt-manager, add CDROM with VirtIO ISO
3. Inside Windows, browse the CD and run:
   ```
   guest-agent\qemu-ga-x86.msi (for 32-bit)
   guest-agent\qemu-ga-x64.msi (for 64-bit)
   ```
4. The service should start automatically
5. Verify from host:
   ```bash
   virsh qemu-agent-command YOUR_VM_NAME '{"execute":"guest-ping"}'
   ```

   Expected output: `{"return":{}}`

### For Linux Guests

```bash
sudo apt install qemu-guest-agent
sudo systemctl enable qemu-guest-agent
sudo systemctl start qemu-guest-agent
```

## Step 4: Install Guest Monitoring Scripts

### For Windows VMs

1. **Download AutoIt**: https://www.autoitscript.com/site/autoit/downloads/
2. Install AutoIt inside the VM
3. Copy scripts from `guest-scripts/windows-xp/` into the VM
4. Edit scripts to set host IP address:
   ```autoit
   Global $HOST_URL = "http://192.168.122.1:8000"
   ```
5. Compile scripts to `.exe` using AutoIt compiler (optional but recommended)
6. Add scripts to Windows Startup folder:
   ```
   C:\Documents and Settings\All Users\Start Menu\Programs\Startup\
   ```

Required scripts:
- `heartbeat.au3` - Sends periodic alive signals
- `idle-monitor.au3` - Detects user inactivity
- `process-watchdog.au3` - Monitors artwork process

### For Mac OS Guests (Future Work)

AppleScript-based monitoring scripts are planned but not yet implemented.

## Step 5: Install Host Controller

### 5.1 Navigate to Host Controller Directory

```bash
cd ~/exhibition-vm-controller/host-controller
```

### 5.2 Install Dependencies

```bash
poetry install
```

### 5.3 Configure the Controller

Copy the example configuration:

```bash
cp examples/config.example.yaml config.yaml
```

Edit `config.yaml`:

```yaml
vm_name: "eden-garden-vm"  # Must match VM name in virt-manager
snapshot_name: "ready"
heartbeat_timeout: 10
idle_timeout: 720  # 12 minutes in seconds
auto_revert_enabled: true
api_port: 8000
check_qemu_agent: true
```

## Step 6: Create the "Ready" Snapshot

Once your VM is fully configured and tested:

### 6.1 Ensure VM is Running

```bash
virsh list --all
```

Your VM should be in the "running" state.

### 6.2 Test Guest Agent

```bash
virsh qemu-agent-command YOUR_VM_NAME '{"execute":"guest-ping"}'
```

### 6.3 Start the Host Controller

```bash
cd ~/exhibition-vm-controller/host-controller
poetry run python -m vm_controller.api
```

### 6.4 Verify Heartbeat is Working

In another terminal:

```bash
curl http://localhost:8000/api/v1/status
```

You should see heartbeat status and timestamps.

### 6.5 Create the Snapshot

```bash
curl -X POST http://localhost:8000/api/v1/snapshot/create
```

Or using virsh directly:

```bash
virsh snapshot-create-as YOUR_VM_NAME ready \
    --description "Ready state for exhibition" \
    --atomic
```

## Step 7: Test Snapshot Revert

### 7.1 Make a Change in the VM

Open Notepad in the VM and type something.

### 7.2 Trigger a Manual Revert

```bash
curl -X POST http://localhost:8000/api/v1/snapshot/revert
```

### 7.3 Observe the Result

The VM should revert within 2-5 seconds, and your notepad text should disappear.

## Step 8: Configure Kiosk Mode Display

### 8.1 Install Openbox

Already installed in prerequisites. If not:

```bash
sudo apt install openbox
```

### 8.2 Configure Autostart

Create autostart file:

```bash
mkdir -p ~/.config/openbox
nano ~/.config/openbox/autostart
```

Add the following:

```bash
#!/bin/bash

# Hide mouse cursor after 5 seconds of inactivity
unclutter -idle 5 &

# Start the host controller
cd ~/exhibition-vm-controller/host-controller
poetry run python -m vm_controller.api &

# Wait for VM to be ready
sleep 10

# Launch virt-viewer in kiosk mode
virt-viewer --full-screen --kiosk --wait YOUR_VM_NAME &
```

Make it executable:

```bash
chmod +x ~/.config/openbox/autostart
```

### 8.3 Configure Auto-Login (Optional)

For exhibition use, you may want automatic login to Openbox.

Edit `/etc/lightdm/lightdm.conf` (if using LightDM):

```ini
[Seat:*]
autologin-user=your_username
autologin-session=openbox
```

Or use `nodm` for a simpler display manager:

```bash
sudo apt install nodm
sudo nano /etc/default/nodm
```

Set:
```
NODM_ENABLED=true
NODM_USER=your_username
NODM_XSESSION=/usr/bin/openbox-session
```

## Step 9: Configure Systemd Service (Production)

For production exhibition use:

### 9.1 Copy Service File

```bash
sudo cp deployment/systemd/exhibition-vm-controller.service \
    /etc/systemd/system/
```

### 9.2 Edit Service File

```bash
sudo nano /etc/systemd/system/exhibition-vm-controller.service
```

Update paths and username.

### 9.3 Enable and Start

```bash
sudo systemctl daemon-reload
sudo systemctl enable exhibition-vm-controller
sudo systemctl start exhibition-vm-controller
sudo systemctl status exhibition-vm-controller
```

## Step 10: Test End-to-End

### 10.1 Reboot the Host

```bash
sudo reboot
```

### 10.2 Verify Everything Starts Automatically

After reboot:
1. Host should auto-login (if configured)
2. Openbox should start
3. Host controller should launch
4. VM should start automatically
5. virt-viewer should display VM in full-screen kiosk mode

### 10.3 Test Error Recovery

Stop heartbeat script in the VM (or kill artwork process).

After 10 seconds, the host should automatically revert the VM.

## Common Configuration Scenarios

### Scenario 1: Multiple VMs on One Host (Not Recommended)

While possible, we recommend one VM per host for fault isolation. If you must:

1. Create separate config files for each VM
2. Run multiple controller instances on different ports
3. Use display switching or multiple monitors

### Scenario 2: Custom Idle Timeout

Different artworks may need different idle timeouts:

- **Short interactions** (2-3 min): Set idle_timeout to 180-240 seconds
- **Long experiences** (10+ min): Set idle_timeout to 900+ seconds
- **No idle reset**: Set idle_timeout to 0 (disable)

### Scenario 3: Offline/Air-Gapped Installation

If host has no internet access:

1. Download all packages on another machine
2. Transfer via USB drive
3. Install using `dpkg -i` and `pip install --no-index`
4. Use local PyPI mirror for Poetry

## Next Steps

- Read [Architecture](architecture.md) to understand the system design
- Review [Heartbeat Protocol](heartbeat-protocol.md) for communication details
- Explore [API Reference](api-reference.md) for programmatic control
- Check [Troubleshooting](troubleshooting.md) if you encounter issues

## Quick Reference

**Start controller**:
```bash
cd ~/exhibition-vm-controller/host-controller
poetry run python -m vm_controller.api
```

**Check VM status**:
```bash
virsh list --all
```

**Test guest agent**:
```bash
virsh qemu-agent-command YOUR_VM_NAME '{"execute":"guest-ping"}'
```

**Create snapshot**:
```bash
virsh snapshot-create-as YOUR_VM_NAME ready --atomic
```

**Revert snapshot**:
```bash
virsh snapshot-revert YOUR_VM_NAME ready
```

**View logs**:
```bash
journalctl -u exhibition-vm-controller -f
```
