# Troubleshooting

This guide covers common issues and their solutions when running the Exhibition VM Controller.

## Table of Contents

- [Installation Issues](#installation-issues)
- [VM Issues](#vm-issues)
- [Heartbeat Issues](#heartbeat-issues)
- [Network Issues](#network-issues)
- [Snapshot Issues](#snapshot-issues)
- [Display Issues](#display-issues)
- [Performance Issues](#performance-issues)
- [Guest Script Issues](#guest-script-issues)

---

## Installation Issues

### KVM not available

**Symptoms**:
```
ERROR: Could not access KVM kernel module
```

**Solution**:
```bash
# Check if CPU supports virtualization
egrep -c '(vmx|svm)' /proc/cpuinfo
# Should return > 0

# Check if KVM modules are loaded
lsmod | grep kvm
# Should show kvm_intel or kvm_amd

# Load KVM modules if missing
sudo modprobe kvm
sudo modprobe kvm_intel  # or kvm_amd

# Make permanent
echo "kvm" | sudo tee -a /etc/modules
echo "kvm_intel" | sudo tee -a /etc/modules  # or kvm_amd
```

If virtualization is not available:
1. Enable VT-x/AMD-V in BIOS
2. Disable Hyper-V on Windows dual-boot systems
3. Use different hardware

---

### Permission denied accessing /dev/kvm

**Symptoms**:
```
ERROR: Could not access KVM: Permission denied
```

**Solution**:
```bash
# Add user to kvm and libvirt groups
sudo usermod -a -G kvm $USER
sudo usermod -a -G libvirt $USER

# Log out and log back in
# Verify membership
groups | grep -E '(kvm|libvirt)'

# Check KVM device permissions
ls -l /dev/kvm
# Should show: crw-rw---- 1 root kvm

# If permissions are wrong
sudo chmod 660 /dev/kvm
sudo chgrp kvm /dev/kvm
```

---

### Poetry installation fails

**Symptoms**:
```
ERROR: Could not install packages due to an OSError
```

**Solution**:
```bash
# Update pip
python3 -m pip install --upgrade pip

# Install system dependencies
sudo apt install python3-dev libvirt-dev

# Try installing again
cd host-controller
poetry install

# If still fails, use venv directly
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt  # if available
```

---

## VM Issues

### VM fails to start

**Symptoms**:
```
ERROR: Domain 'your-vm-name' failed to start
```

**Diagnosis**:
```bash
# Check VM exists
virsh list --all

# Check VM configuration
virsh dumpxml YOUR_VM_NAME

# Check libvirt logs
sudo journalctl -u libvirtd -n 50

# Try starting manually
virsh start YOUR_VM_NAME
```

**Common causes**:
1. **Image file missing**: Check VM storage path exists
2. **Permissions**: Ensure libvirt can access disk images
3. **Network conflict**: Check if network is active: `virsh net-list`
4. **Memory**: Insufficient host RAM
5. **CPU incompatibility**: VM CPU model incompatible with host

**Solutions**:
```bash
# Fix image permissions
sudo chown root:root /var/lib/libvirt/images/your-vm.qcow2
sudo chmod 644 /var/lib/libvirt/images/your-vm.qcow2

# Start default network
virsh net-start default
virsh net-autostart default

# Reduce VM memory allocation
virt-manager  # Edit VM -> Memory

# Change CPU model to "host-passthrough"
virsh edit YOUR_VM_NAME
# Change <cpu mode='...'/> to <cpu mode='host-passthrough'/>
```

---

### VM freezes or becomes unresponsive

**Symptoms**:
- VM display frozen
- No response to mouse/keyboard
- Heartbeat timeout

**Diagnosis**:
```bash
# Check VM state
virsh list

# Check QEMU guest agent
virsh qemu-agent-command YOUR_VM_NAME '{"execute":"guest-ping"}'

# Check host resources
top
free -h
df -h
```

**Solutions**:
```bash
# Manual restart via API
curl -X POST http://localhost:8000/api/v1/vm/restart

# Or via virsh
virsh snapshot-revert YOUR_VM_NAME ready

# Check system logs for out-of-memory errors
sudo dmesg | grep -i "out of memory"

# Increase VM resources if needed
virt-manager  # Edit VM settings
```

---

### Cannot connect to VM display

**Symptoms**:
- virt-viewer shows black screen
- Connection refused error

**Diagnosis**:
```bash
# Check VM is running
virsh list

# Check display settings
virsh dumpxml YOUR_VM_NAME | grep -A 10 "graphics"

# Test connection manually
virt-viewer YOUR_VM_NAME
```

**Solutions**:
```bash
# Ensure graphics are enabled in VM config
virsh edit YOUR_VM_NAME
# Should have <graphics type='spice' .../> or similar

# Restart libvirtd
sudo systemctl restart libvirtd

# Check for firewall blocking
sudo iptables -L | grep 5900  # VNC port
```

---

## Heartbeat Issues

### No heartbeat received from guest

**Symptoms**:
```json
{
  "last_heartbeat": null,
  "is_healthy": false,
  "seconds_since_last": null
}
```

**Diagnosis**:
```bash
# Check API is receiving requests
curl http://localhost:8000/api/v1/status

# Check from inside VM (if possible)
# In Windows guest, open browser to:
http://192.168.122.1:8000/api/v1/heartbeat

# Check host controller logs
journalctl -u exhibition-vm-controller -f

# Check network connectivity from guest
ping 192.168.122.1  # Inside VM
```

**Common causes**:
1. Guest scripts not running
2. Network connectivity issues
3. Wrong host IP in scripts
4. Firewall blocking

**Solutions**:

**In Guest VM**:
```
1. Check heartbeat script is in Startup folder
   C:\Documents and Settings\All Users\Start Menu\Programs\Startup\

2. Run script manually to test
   heartbeat.exe (or heartbeat.au3)

3. Check script has correct host IP
   Global $HOST_URL = "http://192.168.122.1:8000"

4. Test network from command prompt
   ping 192.168.122.1
```

**On Host**:
```bash
# Allow traffic from VM network
sudo iptables -A INPUT -i virbr0 -j ACCEPT

# Or specifically for API port
sudo iptables -A INPUT -i virbr0 -p tcp --dport 8000 -j ACCEPT

# Make permanent (Ubuntu)
sudo apt install iptables-persistent
sudo netfilter-persistent save
```

---

### Heartbeat timing out too frequently

**Symptoms**:
- Frequent automatic reverts
- Logs show many timeout events
- VM appears healthy but gets reset

**Diagnosis**:
```bash
# Check heartbeat status
curl http://localhost:8000/api/v1/heartbeat/status

# Watch real-time
watch -n 1 'curl -s http://localhost:8000/api/v1/heartbeat/status | jq'

# Check for network latency
# Inside guest:
ping -n 100 192.168.122.1
```

**Solutions**:

**Increase timeout**:
```yaml
# In config.yaml
heartbeat_timeout: 20  # Increase from default 10 seconds
```

**Reduce heartbeat interval in guest**:
```autoit
; In heartbeat.au3
Global $HEARTBEAT_INTERVAL = 500  ; Decrease from 1000ms to 500ms
```

**Check for resource contention**:
```bash
# Monitor CPU usage
top

# Check for CPU steal time (host overcommitted)
vmstat 1 10

# Increase VM priority
virsh schedinfo YOUR_VM_NAME
```

---

### False positive heartbeat (guest broken but still sending)

**Symptoms**:
- Heartbeat shows healthy
- But artwork is not functioning
- Process watchdog not detecting

**Solution**:

Add more sophisticated checks:

```autoit
; Check if process exists AND window is visible
If ProcessExists("artwork.exe") And WinExists("[CLASS:WorkWindowClass]") Then
    ; All good
Else
    ; Request revert
    InetGet($HOST_URL & "/api/v1/revert/request?reason=process_check_failed", "", 1)
EndIf
```

---

## Network Issues

### Guest cannot reach host

**Symptoms**:
- ping 192.168.122.1 fails from guest
- No connectivity between VM and host

**Diagnosis**:
```bash
# On host, check libvirt network
virsh net-list --all

# Should show "default" network as active
# If not:
virsh net-start default
virsh net-autostart default

# Check network interface
ip addr show virbr0
# Should have IP 192.168.122.1

# Check VM network settings
virsh dumpxml YOUR_VM_NAME | grep -A 5 "interface"
```

**Solutions**:
```bash
# Restart libvirt network
virsh net-destroy default
virsh net-start default

# Ensure VM uses correct network
virt-manager
# Edit VM -> NIC -> Network source: "Virtual network 'default': NAT"

# Check host firewall
sudo iptables -L -v -n | grep 192.168.122

# Disable firewall temporarily to test
sudo systemctl stop ufw  # Ubuntu
# or
sudo systemctl stop firewalld  # RHEL/CentOS
```

---

### DNS not working in guest

**Symptoms**:
- Can ping 192.168.122.1
- Cannot resolve domain names
- Artwork cannot fetch external content

**Solution**:

**In Guest VM (Windows)**:
```
1. Open Network Connections
2. Right-click network adapter -> Properties
3. Select "Internet Protocol (TCP/IP)"
4. Click Properties
5. Use these DNS servers:
   - Primary: 192.168.122.1 (uses host's DNS)
   - Alternative: 8.8.8.8 (Google DNS)
```

**On Host**:
```bash
# Enable DNS forwarding for VMs
virsh net-edit default

# Ensure <dns> section exists:
<network>
  <name>default</name>
  ...
  <dns enable='yes'/>
  ...
</network>

# Restart network
virsh net-destroy default
virsh net-start default
```

---

## Snapshot Issues

### Snapshot creation fails

**Symptoms**:
```
ERROR: Operation not supported: live disk snapshot not supported
```

**Solution**:
```bash
# Use --disk-only flag for running VMs
virsh snapshot-create-as YOUR_VM_NAME snapshot-name --disk-only

# Or pause VM during snapshot
virsh suspend YOUR_VM_NAME
virsh snapshot-create-as YOUR_VM_NAME snapshot-name
virsh resume YOUR_VM_NAME

# Check disk format (must be qcow2 for internal snapshots)
virsh dumpxml YOUR_VM_NAME | grep "source file"
qemu-img info /path/to/disk.qcow2

# Convert from raw to qcow2 if needed (BACKUP FIRST!)
qemu-img convert -f raw -O qcow2 old.img new.qcow2
```

---

### Snapshot revert is slow

**Symptoms**:
- Revert takes 30+ seconds
- Delays artwork recovery

**Diagnosis**:
```bash
# Check disk backend
virsh dumpxml YOUR_VM_NAME | grep "driver name"

# Check disk usage
qemu-img info /var/lib/libvirt/images/your-vm.qcow2
```

**Solutions**:
```bash
# Use cache='none' or cache='writeback' for better performance
virsh edit YOUR_VM_NAME
# Modify <driver ...cache='writeback'/>

# Use SSD storage for VM images
sudo mv /var/lib/libvirt/images /mnt/ssd/libvirt-images
sudo ln -s /mnt/ssd/libvirt-images /var/lib/libvirt/images

# Reduce snapshot size by minimizing VM RAM
# 2GB is usually sufficient for Windows XP artwork VMs
```

---

### Snapshot does not exist

**Symptoms**:
```
ERROR: Snapshot 'ready' does not exist
```

**Diagnosis**:
```bash
# List snapshots
virsh snapshot-list YOUR_VM_NAME

# Check configured snapshot name
grep snapshot_name host-controller/config.yaml
```

**Solution**:
```bash
# Create the missing snapshot
virsh snapshot-create-as YOUR_VM_NAME ready \
    --description "Ready state for exhibition" \
    --atomic

# Or via API
curl -X POST http://localhost:8000/api/v1/snapshot/create
```

---

## Display Issues

### Virt-viewer not launching in kiosk mode

**Symptoms**:
- Window has borders and controls
- Not fullscreen

**Diagnosis**:
```bash
# Check virt-viewer command
ps aux | grep virt-viewer

# Test manually
virt-viewer --full-screen --kiosk YOUR_VM_NAME
```

**Solution**:
```bash
# Ensure correct flags in autostart script
nano ~/.config/openbox/autostart

# Should have:
virt-viewer --full-screen --kiosk --wait YOUR_VM_NAME &

# Not just:
virt-viewer YOUR_VM_NAME &

# Make executable
chmod +x ~/.config/openbox/autostart
```

---

### Black screen in kiosk mode

**Symptoms**:
- VM is running
- Virt-viewer shows black screen
- Works in virt-manager

**Solution**:
```bash
# Try different display options
virt-viewer --full-screen --kiosk-quit never YOUR_VM_NAME

# Check VM graphics settings
virsh dumpxml YOUR_VM_NAME | grep -A 5 graphics

# Switch from SPICE to VNC or vice versa
virt-manager
# Edit VM -> Display Spice -> Video QXL -> Apply
```

---

### Multiple monitors showing same content

**Symptoms**:
- Duplicate display on multiple screens
- Want different VMs on each

**Solution**:

For multiple artworks on multiple screens:
```bash
# Use separate displays with DISPLAY variable
DISPLAY=:0.0 virt-viewer --full-screen --kiosk vm1 &
DISPLAY=:0.1 virt-viewer --full-screen --kiosk vm2 &

# Or use xrandr to configure displays
xrandr --output HDMI-1 --primary --mode 1920x1080
xrandr --output HDMI-2 --right-of HDMI-1 --mode 1920x1080
```

---

## Performance Issues

### VM running slowly

**Symptoms**:
- Low frame rate
- Sluggish interaction
- Artwork not responsive

**Diagnosis**:
```bash
# Check VM CPU allocation
virsh dumpxml YOUR_VM_NAME | grep vcpu

# Check host CPU usage
top

# Check VM CPU usage (via guest agent)
virsh domstats YOUR_VM_NAME | grep cpu

# Check memory
free -h
virsh dominfo YOUR_VM_NAME | grep memory
```

**Solutions**:
```bash
# Allocate more CPU cores
virt-manager
# Edit VM -> CPUs -> increase count

# Enable CPU pinning for better performance
virsh edit YOUR_VM_NAME
# Add:
<cputune>
  <vcpupin vcpu='0' cpuset='0'/>
  <vcpupin vcpu='1' cpuset='1'/>
</cputune>

# Use host-passthrough for CPU
virsh edit YOUR_VM_NAME
<cpu mode='host-passthrough'/>

# Increase VM RAM
virt-manager
# Edit VM -> Memory -> increase
```

---

### Host system overloaded

**Symptoms**:
- Multiple VMs struggling
- High CPU steal time
- Frequent timeout

**Solution**:

**Option 1: Reduce load**
```bash
# Stop unnecessary services
sudo systemctl disable cups
sudo systemctl disable bluetooth

# Use lighter window manager (already using Openbox - good)

# Disable desktop effects
# Openbox is already minimal
```

**Option 2: Better hardware**
- This architecture assumes one VM per physical host
- For exhibition with many artworks, use multiple hosts
- One physical machine per artwork is ideal

---

## Guest Script Issues

### AutoIt script not starting automatically

**Symptoms**:
- Scripts work when run manually
- Do not start on boot

**Solution**:

**Windows XP**:
```
1. Compile scripts to .exe (recommended):
   - Right-click .au3 file
   - Select "Compile Script (x86)"

2. Place .exe in Startup folder:
   C:\Documents and Settings\All Users\Start Menu\Programs\Startup\

3. Verify:
   - Restart VM
   - Check Task Manager -> Processes
   - Should see heartbeat.exe, idle-monitor.exe, etc.
```

**Alternative: Registry Run key**:
```reg
REGEDIT4

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run]
"Heartbeat"="C:\\Scripts\\heartbeat.exe"
"IdleMonitor"="C:\\Scripts\\idle-monitor.exe"
```

---

### AutoIt scripts crash

**Symptoms**:
- Process starts then disappears
- No error visible

**Diagnosis**:

Add error handling to scripts:

```autoit
; At start of script
#include <MsgBoxConstants.au3>

; Enable error logging
FileWrite("C:\script.log", "Script started: " & @YEAR & "-" & @MON & "-" & @MDAY & @CRLF)

; Wrap main loop in error handler
While True
    Local $error = 0

    ; Your code here
    $response = InetGet($HOST_URL & "/api/v1/heartbeat", "", 1)

    If @error Then
        FileWrite("C:\script.log", "Error: " & @error & @CRLF)
        $error = 1
    EndIf

    Sleep($HEARTBEAT_INTERVAL)
WEnd
```

---

### Script cannot find window/process

**Symptoms**:
- Process watchdog reports false positives
- Window management fails

**Diagnosis**:

```autoit
; Use AutoIt Window Info Tool to get correct identifiers
; Available in AutoIt installation directory

; Debug script to show found windows
$list = WinList()
For $i = 1 To $list[0][0]
    FileWrite("C:\windows.log", $list[$i][0] & " - " & $list[$i][1] & @CRLF)
Next

; Check process list
$processes = ProcessList()
For $i = 1 To $processes[0][0]
    FileWrite("C:\processes.log", $processes[$i][0] & " - " & $processes[$i][1] & @CRLF)
Next
```

---

## Getting Help

If you continue to experience issues:

1. **Check logs**:
   ```bash
   # Host controller logs
   journalctl -u exhibition-vm-controller -n 100

   # Libvirt logs
   sudo journalctl -u libvirtd -n 100

   # System logs
   sudo dmesg | tail -50
   ```

2. **Gather diagnostic info**:
   ```bash
   # System info
   uname -a
   virsh version
   python3 --version

   # VM status
   virsh list --all
   virsh dominfo YOUR_VM_NAME
   virsh snapshot-list YOUR_VM_NAME

   # Network status
   virsh net-list
   ip addr show virbr0
   ```

3. **Open an issue**:
   - GitHub: https://github.com/zkmkarlsruhe/exhibition-vm-controller/issues
   - Include: OS version, error messages, logs, steps to reproduce

4. **Contact**:
   - Email: mschuetze@zkm.de
   - Subject: "Exhibition VM Controller Support"

---

## Common Checklist

Before opening an issue, verify:

- [ ] QEMU guest agent installed and running in VM
- [ ] VM uses correct network (default NAT)
- [ ] "ready" snapshot exists
- [ ] Host controller running: `systemctl status exhibition-vm-controller`
- [ ] Firewall allows traffic on port 8000 from virbr0
- [ ] Guest scripts in Startup folder
- [ ] Scripts have correct host IP (192.168.122.1)
- [ ] config.yaml exists and has correct vm_name
- [ ] User in libvirt and kvm groups
- [ ] libvirt default network active: `virsh net-list`

---

*This troubleshooting guide is continuously updated based on real-world deployment experience.*
