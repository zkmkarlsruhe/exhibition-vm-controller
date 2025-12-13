"""
Plugin system for Exhibition VM Controller.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module provides a plugin registry system that allows external modules
to register custom poll providers and signal handlers without modifying core code.
"""

import importlib.util
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Central registry for poll providers and signal handlers.

    Plugins can register callbacks that are invoked when guests poll for state
    or send signals. Falls back to shell scripts in hooks/ directory if no
    Python plugin handles the request.
    """

    def __init__(self, plugins_dir: Optional[Path] = None, hooks_dir: Optional[Path] = None):
        """
        Initialize plugin registry.

        Args:
            plugins_dir: Directory containing Python plugin modules
            hooks_dir: Directory containing shell script hooks
        """
        self.plugins_dir = plugins_dir or Path("plugins")
        self.hooks_dir = hooks_dir or Path("hooks")

        # Registry dictionaries
        self._poll_providers: Dict[str, Callable[[], str]] = {}
        self._signal_handlers: Dict[str, Callable[[str], None]] = {}

        # State storage for simple plugins that just set values
        self._poll_state: Dict[str, str] = {}

        logger.info(f"Plugin registry initialized (plugins: {self.plugins_dir}, hooks: {self.hooks_dir})")

    def register_poll_provider(self, resource: str, handler: Callable[[], str]) -> None:
        """
        Register a poll provider for a specific resource.

        Args:
            resource: Resource name (e.g., "button", "command")
            handler: Callable that returns current state as string
        """
        self._poll_providers[resource] = handler
        logger.info(f"Registered poll provider for '{resource}'")

    def register_signal_handler(self, event: str, handler: Callable[[str], None]) -> None:
        """
        Register a signal handler for a specific event.

        Args:
            event: Event name (e.g., "ui-state", "application-loaded")
            handler: Callable that processes the signal value
        """
        self._signal_handlers[event] = handler
        logger.info(f"Registered signal handler for '{event}'")

    def get_poll_value(self, resource: str) -> str:
        """
        Get current value for a poll resource.

        Tries in order:
        1. Registered Python poll provider
        2. Stored state value
        3. Shell script hook in hooks/polls/{resource}.sh
        4. Returns "none" if nothing found

        Args:
            resource: Resource name to poll

        Returns:
            Current state as plain text string
        """
        # Try Python poll provider first
        if resource in self._poll_providers:
            try:
                value = self._poll_providers[resource]()
                logger.debug(f"Poll '{resource}' -> '{value}' (Python provider)")
                return value
            except Exception as e:
                logger.error(f"Error in poll provider '{resource}': {e}", exc_info=True)

        # Try stored state
        if resource in self._poll_state:
            value = self._poll_state[resource]
            logger.debug(f"Poll '{resource}' -> '{value}' (stored state)")
            return value

        # Try shell script hook
        hook_path = self.hooks_dir / "polls" / f"{resource}.sh"
        if hook_path.exists() and hook_path.is_file():
            try:
                result = subprocess.run(
                    [str(hook_path)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
                value = result.stdout.strip()
                logger.debug(f"Poll '{resource}' -> '{value}' (shell hook)")
                return value
            except subprocess.TimeoutExpired:
                logger.error(f"Poll hook '{resource}' timed out")
            except subprocess.CalledProcessError as e:
                logger.error(f"Poll hook '{resource}' failed: {e}")
            except Exception as e:
                logger.error(f"Error executing poll hook '{resource}': {e}")

        # Default: return "none"
        logger.debug(f"Poll '{resource}' -> 'none' (no provider found)")
        return "none"

    def handle_signal(self, event: str, value: str) -> None:
        """
        Handle a signal event.

        Tries in order:
        1. Registered Python signal handler
        2. Shell script hook in hooks/signals/{event}.sh
        3. Logs if no handler found

        Args:
            event: Event name
            value: Signal value
        """
        # Try Python signal handler first
        if event in self._signal_handlers:
            try:
                self._signal_handlers[event](value)
                logger.info(f"Signal '{event}' = '{value}' (Python handler)")
                return
            except Exception as e:
                logger.error(f"Error in signal handler '{event}': {e}", exc_info=True)

        # Try shell script hook
        hook_path = self.hooks_dir / "signals" / f"{event}.sh"
        if hook_path.exists() and hook_path.is_file():
            try:
                subprocess.run(
                    [str(hook_path), value],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
                logger.info(f"Signal '{event}' = '{value}' (shell hook)")
                return
            except subprocess.TimeoutExpired:
                logger.error(f"Signal hook '{event}' timed out")
            except subprocess.CalledProcessError as e:
                logger.error(f"Signal hook '{event}' failed: {e}")
            except Exception as e:
                logger.error(f"Error executing signal hook '{event}': {e}")

        # No handler found, just log
        logger.info(f"Signal '{event}' = '{value}' (no handler, logged only)")

    def set_poll_value(self, resource: str, value: str) -> None:
        """
        Set a poll value for simple state storage.

        This is useful for external systems (like Arduino controllers) to
        set state that AutoIt scripts can poll.

        Args:
            resource: Resource name
            value: New value as string
        """
        self._poll_state[resource] = value
        logger.info(f"Poll state '{resource}' set to '{value}'")

    def load_plugins(self) -> None:
        """
        Auto-discover and load Python plugins from plugins directory.

        Each plugin file should have a setup(registry) function that registers
        its poll providers and signal handlers.
        """
        if not self.plugins_dir.exists():
            logger.info(f"Plugins directory not found: {self.plugins_dir}")
            return

        plugin_files = list(self.plugins_dir.glob("*.py"))
        if not plugin_files:
            logger.info(f"No plugin files found in {self.plugins_dir}")
            return

        for plugin_file in plugin_files:
            if plugin_file.name.startswith("_"):
                continue  # Skip __init__.py and private files

            try:
                # Load module
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{plugin_file.stem}",
                    plugin_file,
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Call setup function if it exists
                    if hasattr(module, "setup"):
                        module.setup(self)
                        logger.info(f"Loaded plugin: {plugin_file.name}")
                    else:
                        logger.warning(f"Plugin {plugin_file.name} has no setup() function")
            except Exception as e:
                logger.error(f"Error loading plugin {plugin_file.name}: {e}", exc_info=True)
