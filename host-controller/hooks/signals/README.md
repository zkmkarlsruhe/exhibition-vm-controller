# Signal Hooks

Shell scripts that handle notifications from AutoIt.

## Usage

1. Copy example: `cp ui-state.example.sh ui-state.sh`
2. Make executable: `chmod +x ui-state.sh`
3. Customize logic
4. Restart controller

## Script Requirements

- Must be executable (`chmod +x`)
- Receives value as first argument: `$1`
- Should complete within 5 seconds
- Exit code 0 for success

## Example

```bash
#!/bin/bash
# Log UI state changes
VALUE="$1"
echo "$(date) - UI: $VALUE" >> /var/log/ui-state.log
```

## AutoIt Signaling

```autoit
; Notify host of UI state
HttpGet("http://192.168.122.1:8000/api/v1/signal/ui-state?value=ready")
```

## Git Ignore

All `.sh` files except `*.example.sh` and `README.md` are gitignored.
