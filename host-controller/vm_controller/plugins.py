"""
Plugin system for Exhibition VM Controller.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

Plugins extend the controller by registering handlers and hooks.
All control routes live under /api/v1/ — plugins hook into the core,
they don't create separate API namespaces.
"""

import asyncio
import importlib.util
import json
import logging
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Cap per-client SSE backlog. A stuck/slow client (browser tab that stopped reading) must not grow
# its queue without bound over a long show. When full we drop the OLDEST event to make room for the
# newest, so a laggard sees a gap rather than exhausting host memory.
_SSE_QUEUE_MAXSIZE = 1000


class PluginRegistry:
    """Central registry for plugin capabilities."""

    def __init__(self, plugins_dir: Optional[Path] = None, hooks_dir: Optional[Path] = None):
        self.plugins_dir = plugins_dir or Path("plugins")
        self.hooks_dir = hooks_dir or Path("hooks")

        # Core extension points
        self._poll_providers: Dict[str, Callable[[], str]] = {}
        self._signal_handlers: Dict[str, Callable[[str], None]] = {}
        self._state_providers: Dict[str, Callable[[], dict]] = {}
        self._routers: List[APIRouter] = []

        # Lifecycle hooks
        self._startup_hooks: List[Callable] = []
        self._shutdown_hooks: List[Callable] = []
        self._pre_restart_hooks: List[Callable] = []
        self._post_restart_hooks: List[Callable] = []

        # SSE event queues for connected clients
        self._sse_clients: List[asyncio.Queue] = []

        # Web handler for serving plugin content
        self._web_handler: Optional[Callable] = None

        # Simple key-value state storage (for plugins without Python handlers)
        self._poll_state: Dict[str, str] = {}

        logger.info(
            f"Plugin registry initialized (plugins: {self.plugins_dir}, hooks: {self.hooks_dir})"
        )

    # --- Registration ---

    def register_poll_provider(self, resource: str, handler: Callable[[], str]) -> None:
        self._poll_providers[resource] = handler
        logger.info(f"Registered poll provider for '{resource}'")

    def register_signal_handler(self, event: str, handler: Callable[[str], None]) -> None:
        self._signal_handlers[event] = handler
        logger.info(f"Registered signal handler for '{event}'")

    def register_state_provider(self, name: str, provider: Callable[[], dict]) -> None:
        """Register a state provider. Contributes to /api/v1/state."""
        self._state_providers[name] = provider
        logger.info(f"Registered state provider '{name}'")

    def register_web_handler(self, handler: Callable) -> None:
        """Register a web handler: (path: str) → Response or None."""
        self._web_handler = handler
        logger.info("Registered web handler")

    def register_startup_hook(self, hook: Callable) -> None:
        self._startup_hooks.append(hook)
        logger.info("Registered startup hook")

    def register_shutdown_hook(self, hook: Callable) -> None:
        self._shutdown_hooks.append(hook)
        logger.info("Registered shutdown hook")

    def register_pre_restart_hook(self, hook: Callable[[], bool]) -> None:
        """Pre-restart hook. Return True to allow, or a reason string to block."""
        self._pre_restart_hooks.append(hook)
        logger.info("Registered pre-restart hook")

    def register_post_restart_hook(self, hook: Callable) -> None:
        self._post_restart_hooks.append(hook)
        logger.info("Registered post-restart hook")

    # --- Dispatch ---

    def get_poll_value(self, resource: str) -> str:
        if resource in self._poll_providers:
            try:
                return self._poll_providers[resource]()
            except Exception as e:
                logger.error(f"Error in poll provider '{resource}': {e}", exc_info=True)

        if resource in self._poll_state:
            return self._poll_state[resource]

        # Shell script fallback
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
                return result.stdout.strip()
            except Exception as e:
                logger.error(f"Poll hook '{resource}' failed: {e}")

        return "none"

    def handle_signal(self, event: str, value: str) -> None:
        label = f"Signal '{event}'" + (f" = '{value}'" if value else "")
        handled = False

        if event in self._signal_handlers:
            try:
                self._signal_handlers[event](value)
                logger.info(label)
                handled = True
            except Exception as e:
                logger.error(f"Error in signal handler '{event}': {e}", exc_info=True)
        else:
            # Shell script fallback
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
                    logger.info(f"{label} (shell hook)")
                    handled = True
                except Exception as e:
                    logger.error(f"Signal hook '{event}' failed: {e}")

        if not handled:
            logger.info(f"{label} (no handler)")
            return

        # Push all signals to SSE clients
        if self._sse_clients:
            data = json.dumps({"event": event, "value": value} if value else {"event": event})
            self.push_event("signal", data)

    def get_state(self) -> dict:
        """Aggregate state from all providers."""
        state = {}
        for name, provider in self._state_providers.items():
            try:
                state[name] = provider()
            except Exception as e:
                logger.error(f"Error in state provider '{name}': {e}", exc_info=True)
                state[name] = {"error": str(e)}
        return state

    def set_poll_value(self, resource: str, value: str) -> None:
        self._poll_state[resource] = value

    def check_pre_restart(self) -> Optional[str]:
        for hook in self._pre_restart_hooks:
            try:
                result = hook()
                if result is not True:
                    reason = result if isinstance(result, str) else "Restart blocked by plugin"
                    logger.info(f"Pre-restart hook blocked: {reason}")
                    return reason
            except Exception as e:
                logger.error(f"Error in pre-restart hook: {e}", exc_info=True)
        return None

    def run_post_restart(self) -> None:
        for hook in self._post_restart_hooks:
            try:
                hook()
            except Exception as e:
                logger.error(f"Error in post-restart hook: {e}", exc_info=True)

    # --- SSE ---

    def sse_connect(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
        self._sse_clients.append(queue)
        logger.info(f"SSE client connected ({len(self._sse_clients)} total)")
        return queue

    def sse_disconnect(self, queue: asyncio.Queue) -> None:
        if queue in self._sse_clients:
            self._sse_clients.remove(queue)
        logger.info(f"SSE client disconnected ({len(self._sse_clients)} remaining)")

    def push_event(self, event: str, data: str) -> None:
        """Push an event to all connected SSE clients.

        Queues are bounded (``_SSE_QUEUE_MAXSIZE``). For a laggard whose queue is full we drop the
        oldest event to make room for the newest so its backlog stays bounded; if even that fails
        the client is disconnected.
        """
        dead: List[asyncio.Queue] = []
        for queue in self._sse_clients:
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()  # drop oldest, keep the stream flowing to the latest state
                    queue.put_nowait((event, data))
                except Exception:
                    dead.append(queue)
        for queue in dead:
            logger.warning("Dropping unresponsive SSE client (queue full)")
            self.sse_disconnect(queue)

    @property
    def sse_client_count(self) -> int:
        return len(self._sse_clients)

    # --- Accessors ---

    def serve_web(self, path: str):
        """Try to serve a web path via plugin handler. Returns Response or None."""
        if self._web_handler:
            try:
                return self._web_handler(path)
            except Exception as e:
                logger.error(f"Error in web handler for '{path}': {e}")
        return None

    def get_startup_hooks(self) -> List[Callable]:
        return self._startup_hooks

    def get_shutdown_hooks(self) -> List[Callable]:
        return self._shutdown_hooks

    # --- Plugin loading ---

    def load_plugins(self, plugin_names: Optional[List[str]] = None) -> None:
        """Load plugins. If plugin_names is set, only load those. Otherwise load all."""
        if not self.plugins_dir.exists():
            logger.info(f"Plugins directory not found: {self.plugins_dir}")
            return

        plugin_files = sorted(self.plugins_dir.glob("*.py"))
        for plugin_file in plugin_files:
            if plugin_file.name.startswith("_"):
                continue
            if plugin_names is not None and plugin_file.stem not in plugin_names:
                logger.debug(f"Skipping plugin {plugin_file.name} (not in config)")
                continue

            try:
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{plugin_file.stem}",
                    plugin_file,
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    if hasattr(module, "setup"):
                        module.setup(self)
                        logger.info(f"Loaded plugin: {plugin_file.name}")
                    else:
                        logger.warning(f"Plugin {plugin_file.name} has no setup() function")
            except Exception as e:
                logger.error(f"Error loading plugin {plugin_file.name}: {e}", exc_info=True)
