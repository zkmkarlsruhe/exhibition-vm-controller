# Poll Hooks

Shell scripts that return state when polled by AutoIt.

## Usage

1. Copy example: `cp button.example.sh button.sh`
2. Make executable: `chmod +x button.sh`
3. Customize logic
4. Restart controller

## Script Requirements

- Must be executable (`chmod +x`)
- Must output single-word state to stdout
- Should complete within 5 seconds
- Exit code 0 for success

## Example

```bash
#!/bin/bash
# Return button state
echo "pressed"  # or "released", "on", "off", etc.
```

## AutoIt Polling

```autoit
; Poll button state
$response = HttpGet("http://192.168.122.1:8000/api/v1/poll/button")
If $response == "pressed" Then
    ; Do something
EndIf
```

## Git Ignore

All `.sh` files except `*.example.sh` and `README.md` are gitignored.
