# Custom Plugins Directory

This directory is for custom poll providers and signal handlers that extend the Exhibition VM Controller without modifying core code.

## Plugin Structure

Each plugin is a Python file with a `setup(registry)` function:

```python
def setup(registry):
    """Register poll providers and signal handlers."""
    registry.register_poll_provider("resource", handler_function)
    registry.register_signal_handler("event", handler_function)
```

## Poll Providers

Poll providers return current state as a string when AutoIt scripts query them:

```python
def get_button_state() -> str:
    # Read from hardware, database, file, etc.
    return "pressed"  # or "released", "on", "off", etc.

def setup(registry):
    registry.register_poll_provider("button", get_button_state)
```

AutoIt polls: `GET /api/v1/poll/button` → Returns: `pressed`

## Signal Handlers

Signal handlers receive notifications from AutoIt scripts:

```python
def handle_ui_state(value: str) -> None:
    # Log, save to database, trigger actions, etc.
    print(f"UI state changed to: {value}")

def setup(registry):
    registry.register_signal_handler("ui-state", handle_ui_state)
```

AutoIt signals: `GET /api/v1/signal/ui-state?value=ready`

## Examples

See `example_button.example.py` for a complete example.

To use an example:
```bash
cp example_button.example.py button.py
# Edit button.py to customize
# Restart controller to load
```

## Git Ignore

All `.py` files except `__init__.py`, `*.example.py`, and `README.md` are gitignored, so your custom plugins stay local to your installation.
