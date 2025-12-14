# API Reference

The Exhibition VM Controller provides a REST API built with FastAPI for managing VMs and monitoring their health. This document describes all available endpoints.

## Base URL

Default: `http://localhost:8000`

Configurable via `config.yaml`:
```yaml
api_host: "0.0.0.0"
api_port: 8000
```

## API Documentation

The API provides automatic interactive documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI Schema**: http://localhost:8000/openapi.json

## Authentication

Currently, the API has no authentication. It is designed for use in isolated exhibition environments where the host and VM are on a private network.

## Endpoints

### Root

#### GET /

Get API information and version.

**Response**:
```json
{
  "message": "Exhibition VM Controller API",
  "details": {
    "version": "1.3.0",
    "documentation": "/docs",
    "status": "/api/v1/status"
  }
}
```

---

### Status and Monitoring

#### GET /api/v1/status

Get comprehensive system status including VM state, snapshot availability, and heartbeat status.

**Response**:
```json
{
  "vm_name": "eden-garden-vm",
  "vm_state": "running",
  "vm_is_running": true,
  "snapshot_name": "ready",
  "snapshot_exists": true,
  "heartbeat": {
    "enabled": true,
    "last_heartbeat": "2025-12-09T10:30:45.123456",
    "seconds_since_last": 2.5,
    "is_healthy": true,
    "timeout": 10
  },
  "auto_revert_enabled": true
}
```

**Fields**:
- `vm_name`: Name of the VM being controlled
- `vm_state`: Current VM state (e.g., "running", "shut off", "paused")
- `vm_is_running`: Boolean indicating if VM is currently running
- `snapshot_name`: Name of the reference snapshot for revert operations
- `snapshot_exists`: Boolean indicating if the reference snapshot exists
- `heartbeat`: Detailed heartbeat monitoring information
- `auto_revert_enabled`: Whether automatic snapshot revert is enabled

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error retrieving status

---

#### GET /api/v1/heartbeat/status

Get detailed heartbeat monitoring status.

**Response**:
```json
{
  "enabled": true,
  "last_heartbeat": "2025-12-09T10:30:45.123456",
  "seconds_since_last": 2.5,
  "is_healthy": true,
  "timeout": 10
}
```

**Fields**:
- `enabled`: Whether heartbeat monitoring is active
- `last_heartbeat`: ISO 8601 timestamp of last received heartbeat
- `seconds_since_last`: Seconds elapsed since last heartbeat
- `is_healthy`: Boolean indicating if within timeout threshold
- `timeout`: Configured timeout in seconds

---

### Heartbeat

#### GET|POST /api/v1/heartbeat

Receive heartbeat signal from guest VM. Called periodically by monitoring scripts inside the VM.

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Query Parameters**: None required (optional parameters can be added for future extensions)

**Response**:
```json
{
  "message": "Heartbeat received",
  "details": {
    "enabled": true,
    "last_heartbeat": "2025-12-09T10:30:47.654321",
    "seconds_since_last": 0.0,
    "is_healthy": true,
    "timeout": 10
  }
}
```

**Usage from Guest**:

```bash
# GET request (AutoIt compatible)
curl http://192.168.122.1:8000/api/v1/heartbeat

# POST request
curl -X POST http://192.168.122.1:8000/api/v1/heartbeat

# AutoIt (uses GET)
$oHTTP.Open("GET", "http://192.168.122.1:8000/api/v1/heartbeat", False)
$oHTTP.Send()

# Python
import requests
requests.get("http://192.168.122.1:8000/api/v1/heartbeat")
# or
requests.post("http://192.168.122.1:8000/api/v1/heartbeat")
```

**Errors**:
- `503 Service Unavailable`: Heartbeat monitor not initialized

---

### VM Control

#### GET|POST /api/v1/vm/start

Start the VM by reverting to the configured snapshot.

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Response**:
```json
{
  "message": "VM 'eden-garden-vm' started successfully",
  "details": null
}
```

**Behavior**:
- Reverts to the configured snapshot (e.g., "ready")
- Starts the VM if it was stopped
- Resets heartbeat monitoring

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error starting VM

---

#### GET|POST /api/v1/vm/stop

Stop (destroy) the VM.

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Response**:
```json
{
  "message": "VM 'eden-garden-vm' stopped successfully",
  "details": null
}
```

**Behavior**:
- Disables heartbeat monitoring
- Stops the VM (equivalent to pulling the power plug)
- Does not save state

**Warning**: This immediately stops the VM. Use for emergency situations or maintenance.

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error stopping VM

---

#### GET|POST /api/v1/vm/restart

Restart the VM by reverting to snapshot (recommended recovery method).

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Response**:
```json
{
  "message": "VM 'eden-garden-vm' restarted successfully",
  "details": null
}
```

**Behavior**:
1. Disables heartbeat monitoring
2. Reverts VM to configured snapshot
3. Starts VM
4. Waits configured delay (default: 30 seconds)
5. Re-enables heartbeat monitoring

This is the same operation triggered automatically on heartbeat timeout.

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error restarting VM

---

### Snapshot Management

#### GET /api/v1/snapshots

List all snapshots for the configured VM.

**Response**:
```json
{
  "vm_name": "eden-garden-vm",
  "snapshots": [
    "ready",
    "backup-2025-12-01",
    "test-configuration"
  ]
}
```

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error listing snapshots

---

#### GET|POST /api/v1/snapshot/create

Create a new snapshot or update an existing one.

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Query Parameters**:
- `snapshot_name` (optional): Name for the snapshot. If omitted, uses the configured snapshot name (typically "ready").

**Example**:
```bash
# Create/update default "ready" snapshot (GET)
curl "http://localhost:8000/api/v1/snapshot/create"

# Create named snapshot (GET with parameter)
curl "http://localhost:8000/api/v1/snapshot/create?snapshot_name=backup-2025-12-09"

# POST method also supported
curl -X POST "http://localhost:8000/api/v1/snapshot/create?snapshot_name=backup-2025-12-09"
```

**Response**:
```json
{
  "message": "Snapshot 'ready' created successfully for VM 'eden-garden-vm'",
  "details": null
}
```

**Behavior**:
- If snapshot with same name exists, it is replaced
- Snapshot captures current VM memory and disk state
- VM continues running during snapshot creation

**Important**: Always test the VM thoroughly before creating the "ready" snapshot. This snapshot becomes the reference state for all automatic reverts.

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error creating snapshot

---

#### GET|DELETE /api/v1/snapshot/{snapshot_name}

Delete a specific snapshot.

**Methods**:
- GET at `/api/v1/snapshot/delete/{snapshot_name}` (AutoIt compatible)
- DELETE at `/api/v1/snapshot/{snapshot_name}` (REST standard)

**Path Parameters**:
- `snapshot_name`: Name of the snapshot to delete

**Example**:
```bash
# GET method (AutoIt compatible)
curl "http://localhost:8000/api/v1/snapshot/delete/backup-2025-12-01"

# DELETE method (REST standard)
curl -X DELETE "http://localhost:8000/api/v1/snapshot/backup-2025-12-01"
```

**Response**:
```json
{
  "message": "Snapshot 'backup-2025-12-01' deleted successfully",
  "details": null
}
```

**Warning**: Cannot delete the snapshot currently in use. Deleting the "ready" snapshot will prevent automatic recovery until a new one is created.

**Errors**:
- `503 Service Unavailable`: VM manager not initialized
- `500 Internal Server Error`: Error deleting snapshot (e.g., snapshot not found)

---

### Auto-Revert Control

#### GET|POST /api/v1/revert/enable

Enable automatic snapshot revert on heartbeat timeout.

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Response**:
```json
{
  "message": "Automatic revert enabled",
  "details": null
}
```

**Behavior**:
When enabled (default), the system automatically reverts to the configured snapshot when:
- Heartbeat timeout occurs (default: 10 seconds)
- Guest explicitly requests revert

---

#### GET|POST /api/v1/revert/disable

Disable automatic snapshot revert. Use for maintenance or manual control.

**Methods**: GET, POST (both supported for AutoIt compatibility)

**Response**:
```json
{
  "message": "Automatic revert disabled - manual intervention required on failures",
  "details": null
}
```

**Use Cases**:
- Performing maintenance inside the VM
- Testing new configurations
- Debugging issues
- Creating new snapshots

**Important**: When disabled, heartbeat timeouts will be logged but no automatic recovery will occur. Manual intervention is required.

---

## Error Responses

All endpoints may return standard HTTP error codes:

### 400 Bad Request
Invalid parameters or request format.

```json
{
  "detail": "Invalid parameter: snapshot_name cannot be empty"
}
```

### 404 Not Found
Endpoint does not exist.

```json
{
  "detail": "Not Found"
}
```

### 500 Internal Server Error
Unexpected server error.

```json
{
  "detail": "Error starting VM: Failed to revert snapshot"
}
```

### 503 Service Unavailable
Service not ready (typically during startup).

```json
{
  "detail": "VM manager not initialized"
}
```

---

## Usage Examples

### Check System Health

```bash
curl http://localhost:8000/api/v1/status | jq
```

### Manual Recovery

```bash
# Restart VM to known-good state
curl -X POST http://localhost:8000/api/v1/vm/restart
```

### Maintenance Mode

```bash
# Disable auto-revert
curl -X POST http://localhost:8000/api/v1/revert/disable

# Do maintenance work...
# (VM will not be automatically reset during this time)

# Re-enable auto-revert
curl -X POST http://localhost:8000/api/v1/revert/enable
```

### Create Backup Snapshot

```bash
# Create timestamped backup
TIMESTAMP=$(date +%Y-%m-%d-%H%M)
curl -X POST "http://localhost:8000/api/v1/snapshot/create?snapshot_name=backup-$TIMESTAMP"

# List all snapshots
curl http://localhost:8000/api/v1/snapshots | jq
```

### Monitor Heartbeat

```bash
# Watch heartbeat status in real-time
watch -n 1 'curl -s http://localhost:8000/api/v1/heartbeat/status | jq'
```

### Python Client Example

```python
import requests
import time

BASE_URL = "http://localhost:8000"

def get_status():
    """Get system status."""
    response = requests.get(f"{BASE_URL}/api/v1/status")
    response.raise_for_status()
    return response.json()

def restart_vm():
    """Restart VM to known-good state."""
    response = requests.post(f"{BASE_URL}/api/v1/vm/restart")
    response.raise_for_status()
    return response.json()

def create_snapshot(name="ready"):
    """Create snapshot."""
    response = requests.post(
        f"{BASE_URL}/api/v1/snapshot/create",
        params={"snapshot_name": name}
    )
    response.raise_for_status()
    return response.json()

# Example usage
if __name__ == "__main__":
    status = get_status()
    print(f"VM: {status['vm_name']}")
    print(f"Running: {status['vm_is_running']}")
    print(f"Healthy: {status['heartbeat']['is_healthy']}")

    if not status['heartbeat']['is_healthy']:
        print("Heartbeat unhealthy, restarting VM...")
        restart_vm()
        time.sleep(30)  # Wait for VM to stabilize
```

---

## WebSocket Support

Currently not implemented. Future versions may add WebSocket support for real-time status updates and event streaming.

---

## Rate Limiting

No rate limiting is currently implemented. The API is designed for low-frequency control operations and high-frequency heartbeat signals (1 per second).

---

## CORS

CORS is not enabled by default. If you need to access the API from a web browser in a different origin, add CORS middleware to the FastAPI app.

---

## Logging

All API requests and VM operations are logged. Default log location:

- **Systemd service**: `journalctl -u exhibition-vm-controller`
- **Manual execution**: Console output

Configure logging in `config.yaml`:

```yaml
log_level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

---

## Configuration Reference

See `host-controller/examples/config.example.yaml` for all configuration options:

```yaml
# VM Configuration
vm_name: "your-vm-name"
snapshot_name: "ready"

# Heartbeat Settings
heartbeat_timeout: 10  # seconds
heartbeat_check_interval: 1  # seconds
vm_startup_heartbeat_delay: 30  # seconds to wait after restart

# API Settings
api_host: "0.0.0.0"
api_port: 8000
api_reload: false  # Auto-reload on code changes (dev only)

# Auto-Revert
auto_revert_enabled: true

# Logging
log_level: "INFO"
```

---

## Security Considerations

**No Authentication**: The API has no built-in authentication. It should only be accessible from trusted networks (e.g., the VM's internal network interface).

**Firewall**: Restrict access to the API port:
```bash
# Allow only from libvirt network
sudo iptables -A INPUT -i virbr0 -p tcp --dport 8000 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8000 -j DROP
```

**HTTPS**: Not implemented by default. For production, consider using a reverse proxy (nginx) with TLS.

---

## Troubleshooting

**503 Service Unavailable**:
- Check if API is fully started: `journalctl -u exhibition-vm-controller`
- Verify config.yaml exists and is valid

**Cannot connect from guest**:
- Verify host IP: `ip addr show virbr0`
- Test from host first: `curl http://localhost:8000/api/v1/status`
- Check firewall rules
- Verify libvirt network is active: `virsh net-list`

**Revert operations fail**:
- Check snapshot exists: `virsh snapshot-list YOUR_VM_NAME`
- Verify libvirt permissions: `groups | grep libvirt`
- Check disk space: `df -h`

For more troubleshooting, see [Troubleshooting Guide](troubleshooting.md).
