# Openbox Display Configuration

Configuration for running the VM display in kiosk mode using Openbox window manager and virt-viewer.

## Overview

This setup provides:
- **Kiosk mode**: Full-screen VM display without desktop UI
- **Auto-start**: VM display launches on boot
- **Minimal overhead**: Lightweight Openbox window manager
- **Reliable**: Automatic reconnection if VM restarts

## System Architecture

```
Boot → Auto-login → Openbox → autostart script → virt-viewer --kiosk
```

## Prerequisites

### Install Required Packages

```bash
sudo apt-get update
sudo apt-get install -y openbox virt-viewer unclutter xorg
```

**Packages**:
- `openbox`: Lightweight window manager
- `virt-viewer`: VM display client with kiosk mode
- `unclutter`: Hides mouse cursor after inactivity
- `xorg`: X Window System

## Installation

### 1. Install Openbox

```bash
sudo apt-get install openbox
```

### 2. Configure Auto-login (Optional but Recommended)

For unattended operation, configure automatic login.

#### Using LightDM (Ubuntu/Debian)

Edit `/etc/lightdm/lightdm.conf`:

```ini
[Seat:*]
autologin-user=YOUR_USERNAME
autologin-user-timeout=0
user-session=openbox
```

Then:
```bash
sudo systemctl enable lightdm
```

#### Using GDM (Alternative)

Edit `/etc/gdm3/custom.conf` or `/etc/gdm/custom.conf`:

```ini
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=YOUR_USERNAME
```

#### Console Auto-login (Minimal Setup)

Edit `/etc/systemd/system/getty@tty1.service.d/autologin.conf`:

```ini
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin YOUR_USERNAME --noclear %I $TERM
```

Then add to `~/.bash_profile`:
```bash
if [[ -z $DISPLAY ]] && [[ $(tty) = /dev/tty1 ]]; then
    exec startx
fi
```

### 3. Configure Openbox Autostart

```bash
# Create openbox config directory
mkdir -p ~/.config/openbox

# Copy autostart file
cp autostart ~/.config/openbox/

# Make executable
chmod +x ~/.config/openbox/autostart

# Edit to set your VM name
nano ~/.config/openbox/autostart
```

**Important**: Change `VM_NAME` to match your libvirt VM:

```bash
VM_NAME="your-vm-name"
```

### 4. Test Openbox Session

```bash
# Start Openbox manually to test
openbox-session
```

You should see virt-viewer launch in fullscreen after a few seconds.

## Configuration Options

### Basic virt-viewer Kiosk Mode

```bash
virt-viewer --kiosk --full-screen --reconnect --wait "$VM_NAME"
```

**Options**:
- `--kiosk`: Fullscreen kiosk mode (no menus, no window decorations)
- `--full-screen`: Start in fullscreen
- `--reconnect`: Automatically reconnect if VM restarts
- `--wait`: Wait for VM to start if not running

### Advanced Options

#### Custom Hotkeys

```bash
virt-viewer --kiosk --hotkeys=release-cursor=ctrl+alt --full-screen "$VM_NAME"
```

Release cursor with Ctrl+Alt instead of Ctrl+Alt+Shift.

#### Zoom to Fit

```bash
virt-viewer --kiosk --full-screen --zoom=auto-fit "$VM_NAME"
```

Automatically scale VM display to fit screen.

#### Multiple Monitors

```bash
virt-viewer --kiosk --full-screen --display=:0 "$VM_NAME"
```

### Screen Power Management

The autostart script disables screen blanking:

```bash
xset s off        # Disable screen saver
xset -dpms        # Disable power management
xset s noblank    # Don't blank screen
```

For permanent configuration, add to `/etc/X11/xorg.conf.d/10-monitor.conf`:

```
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
```

### Hide Mouse Cursor

Using `unclutter`:

```bash
unclutter -idle 1 -root &
```

This hides the cursor after 1 second of inactivity.

## VM Management Workflow

### For Configuration/Maintenance

1. **Switch to TTY2**: Press `Ctrl+Alt+F2`
2. **Login** with your credentials
3. **Use virt-manager**:
   ```bash
   DISPLAY=:0 virt-manager
   ```
4. **Make changes** to VM
5. **Create snapshot** via API or virt-manager
6. **Return to display**: Press `Ctrl+Alt+F1`

### Emergency Access

If the kiosk display is stuck:

1. **Switch TTY**: `Ctrl+Alt+F2`
2. **Login**
3. **Restart display**:
   ```bash
   pkill -9 virt-viewer
   DISPLAY=:0 openbox-session &
   ```
4. **Or restart everything**:
   ```bash
   sudo systemctl restart lightdm
   ```

## Troubleshooting

### virt-viewer Doesn't Start

1. **Check VM is running**:
   ```bash
   virsh list --all
   ```

2. **Test virt-viewer manually**:
   ```bash
   virt-viewer your-vm-name
   ```

3. **Check autostart script**:
   ```bash
   cat ~/.config/openbox/autostart
   ```

4. **View Openbox logs**:
   ```bash
   tail -f ~/.xsession-errors
   ```

### Black Screen

1. **Check X is running**:
   ```bash
   ps aux | grep X
   ```

2. **Check display variable**:
   ```bash
   echo $DISPLAY
   ```

3. **Test X manually**:
   ```bash
   DISPLAY=:0 xterm
   ```

### VM Display Doesn't Fill Screen

Add to virt-viewer command:
```bash
virt-viewer --kiosk --full-screen --zoom=auto-fit "$VM_NAME"
```

Or configure VM video driver:
- Use QXL or VirtIO-GPU for better scaling
- Adjust VM resolution to match physical display

### Auto-login Not Working

1. **Check display manager**:
   ```bash
   systemctl status lightdm  # or gdm
   ```

2. **Verify configuration**:
   ```bash
   cat /etc/lightdm/lightdm.conf
   ```

3. **Check user session**:
   ```bash
   ls /usr/share/xsessions/
   ```

   Should have `openbox.desktop`.

## Alternative Display Methods

### Using VNC

If you prefer VNC over virt-viewer:

```bash
# In autostart
vncviewer -fullscreen localhost:5900 &
```

Configure VM to use VNC in virt-manager.

### Using SPICE

virt-viewer uses SPICE by default (if available). For better performance:

1. **Configure VM**: Use SPICE display in virt-manager
2. **Install guest tools**: spice-vdagent in Windows guest
3. **Use virt-viewer** as configured above

### Using SDL

For very lightweight display (no network):

```bash
# Configure VM to use SDL graphics
virsh edit your-vm-name
```

## Security Considerations

### Restrict Access

- **Disable TTY switching**: Edit `/etc/X11/xorg.conf.d/90-prevent-vt-switch.conf`:
  ```
  Section "ServerFlags"
      Option "DontVTSwitch" "true"
  EndSection
  ```

- **Lock down user**: Use restrictive shell or limited sudo

- **Network isolation**: Ensure VM network is isolated

### Physical Security

For public exhibitions:
- **Lock keyboard shortcuts**: Modify openbox `rc.xml`
- **Use kiosk hardware**: Consider kiosk-mode keyboards
- **Physical security**: Secure host machine

## Performance Optimization

### Reduce Overhead

```bash
# Minimal autostart (no unclutter, no extras)
xset s off -dpms
virt-viewer --kiosk --full-screen "$VM_NAME" &
```

### Graphics Performance

- Use **QXL** or **VirtIO-GPU** in VM
- Allocate sufficient video RAM (64-128MB)
- Enable **3D acceleration** if supported

## Testing

### Test Autostart Script

```bash
# Run autostart manually
bash ~/.config/openbox/autostart
```

### Test Full Boot Sequence

```bash
sudo reboot
```

Monitor boot process and verify:
1. Auto-login occurs
2. Openbox starts
3. virt-viewer launches in kiosk mode
4. VM display appears fullscreen

## Author

Marc Schütze (mschuetze@zkm.de)
ZKM | Center for Art and Media Karlsruhe

## License

MIT License - See repository LICENSE file
