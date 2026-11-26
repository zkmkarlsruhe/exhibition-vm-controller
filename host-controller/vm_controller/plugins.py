"""
Plugin system for Exhibition VM Controller.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module is now a THIN exhibition-side adapter over the shared plugin runtime
in ``vmctl_core.plugins``. The registry surface (register_*/dispatch/SSE) lives in
the core, so a plugin authored against the shared spec in the research workbench
loads UNCHANGED here.

The exhibition controller keeps two local extensions on top of the shared core,
neither of which changes the shared contract:

  * dual plugin discovery — manifest-described plugin DIRECTORIES (the shared
    spec, via ``vmctl_core.plugins.load_plugins``) AND legacy flat ``plugins/*.py``
    files (the exhibition's original convention; e.g. ``my_work.py``);
  * shell-script hook fallbacks under ``hooks/`` for poll/signal resources that
    have no Python handler (operator convenience, exhibition-only).

Dependency arrow: exhibition -> vmctl-core only (never -> research).
"""

import importlib.util
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Optional

# The shared runtime — the SAME class both controllers use. Re-exported so
# existing imports (`from vm_controller.plugins import PluginRegistry`) and any
# plugin doing `from vmctl_core.plugins import ...` resolve to one surface.
from vmctl_core.plugins import (  # noqa: F401  (re-exported)
    MANIFEST_FILENAME,
    LoadedPlugin,
    PluginManifest,
)
from vmctl_core.plugins import PluginRegistry as _CorePluginRegistry
from vmctl_core.plugins import load_plugins as _load_manifest_plugins

logger = logging.getLogger(__name__)


class PluginRegistry(_CorePluginRegistry):
    """Exhibition registry: the shared core registry + exhibition-only discovery
    and shell-hook fallbacks. The registration/dispatch contract is inherited
    unchanged from ``vmctl_core.plugins.PluginRegistry``."""

    def __init__(self, plugins_dir: Optional[Path] = None, hooks_dir: Optional[Path] = None):
        super().__init__()
        self.plugins_dir = Path(plugins_dir) if plugins_dir else Path("plugins")
        self.hooks_dir = Path(hooks_dir) if hooks_dir else Path("hooks")
        logger.info(
            f"Plugin registry initialized (plugins: {self.plugins_dir}, hooks: {self.hooks_dir})"
        )

    # --- dispatch with exhibition-only shell-hook fallbacks ----------------

    def get_poll_value(self, resource: str) -> str:
        """Python poll provider → last set value → ``hooks/polls/<r>.sh`` → 'none'."""
        if resource in self._poll_providers:
            try:
                return self._poll_providers[resource]()
            except Exception as e:
                logger.error(f"Error in poll provider '{resource}': {e}", exc_info=True)
        if resource in self._poll_state:
            return self._poll_state[resource]

        hook_path = self.hooks_dir / "polls" / f"{resource}.sh"
        if hook_path.is_file():
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

    def handle_signal(self, event: str, value: str) -> bool:
        """Dispatch to a Python handler (shared core); if none, try a shell hook
        at ``hooks/signals/<event>.sh``. Returns True if handled."""
        if event in self._signal_handlers:
            return super().handle_signal(event, value)

        hook_path = self.hooks_dir / "signals" / f"{event}.sh"
        if hook_path.is_file():
            try:
                subprocess.run(
                    [str(hook_path), value],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
                logger.info(f"Signal '{event}' (shell hook)")
                if self._sse_clients:
                    self.push_event("signal", json.dumps({"event": event, "value": value}))
                return True
            except Exception as e:
                logger.error(f"Signal hook '{event}' failed: {e}")
        logger.info(f"Signal '{event}' (no handler)")
        return False

    @property
    def sse_client_count(self) -> int:
        return len(self._sse_clients)

    # --- plugin loading (dual: shared-spec dirs + legacy flat files) -------

    def load_plugins(self, plugin_names: Optional[List[str]] = None) -> None:
        """Load plugins from ``self.plugins_dir``.

        Two coexisting conventions are discovered, in this order:

        1. **Shared-spec plugin directories** — any ``<plugins_dir>/<name>/`` that
           contains ``manifest.yaml``. Loaded through the shared core loader, so
           a plugin promoted from a research project runs here unchanged. This is
           the format the promotion flow ships (see research/29-promotion-flow).
        2. **Legacy flat plugins** — ``<plugins_dir>/*.py`` exposing ``setup()``
           (the exhibition's original convention; e.g. ``my_work.py``).

        ``plugin_names`` (config ``plugins:``) filters BOTH: a manifest plugin is
        matched by its manifest ``id``, a flat plugin by its file stem. If None,
        everything is loaded.
        """
        if not self.plugins_dir.exists():
            logger.info(f"Plugins directory not found: {self.plugins_dir}")
            return

        # 1) shared-spec plugin directories (manifest.yaml present)
        try:
            _load_manifest_plugins(self.plugins_dir, self, only=plugin_names)
        except Exception as e:
            logger.error(f"Error loading manifest plugins: {e}", exc_info=True)

        # 2) legacy flat plugins/*.py
        for plugin_file in sorted(self.plugins_dir.glob("*.py")):
            if plugin_file.name.startswith("_"):
                continue
            if plugin_names is not None and plugin_file.stem not in plugin_names:
                logger.debug(f"Skipping plugin {plugin_file.name} (not in config)")
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{plugin_file.stem}", plugin_file
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
