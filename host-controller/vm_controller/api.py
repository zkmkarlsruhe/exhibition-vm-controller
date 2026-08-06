"""
FastAPI REST API for Exhibition VM Controller.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module provides a REST API for controlling virtual machines in exhibition
environments, including VM lifecycle management, snapshot operations, and
heartbeat monitoring.
"""

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vm_controller.config import Config
from vm_controller.heartbeat_monitor import HeartbeatMonitor
from vm_controller.plugins import PluginRegistry
from vm_controller.vm_manager import VMManager

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Resolve the real client IP, honouring a trusted reverse proxy.

    The bundled nginx proxies guest traffic to the FastAPI instance on 127.0.0.1, so
    ``request.client.host`` is the proxy (127.0.0.1) — NOT the guest. Reading it directly makes the
    guest-origin guard useless behind nginx (guest content could reach /snapshot/delete/ready).

    When the direct peer is a configured trusted proxy we take the forwarded address from
    ``X-Real-IP`` (nginx sets it to the real ``$remote_addr``, overwriting anything the guest sent),
    falling back to the last hop of ``X-Forwarded-For``. On a direct-bind setup the peer is the
    guest itself and is not in ``trusted_proxies``, so the headers are ignored (un-spoofable).
    """
    direct = request.client.host if request.client else ""
    trusted = list(getattr(config, "trusted_proxies", None) or [])
    if direct and direct in trusted:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The trusted proxy appends the real peer last; take the rightmost, un-spoofable hop.
            return forwarded.split(",")[-1].strip()
    return direct


def _deny_from_vm(request: Request) -> None:
    """Block destructive operations originating from the guest network.

    The guest runs a legacy browser (IE6, Flash, Director) pointed at the host.
    A stray URL in archived/proxied artwork content — e.g. an <img> tag hitting
    /api/v1/snapshot/delete/ready — must never be able to destroy the golden
    snapshot or stop the VM. Operator/system requests from outside the guest
    subnet are always allowed. Heartbeat/signal/poll endpoints are NOT guarded
    (the guest legitimately calls those); /vm/restart keeps its own phase gate.

    Fails CLOSED (denies) if the VM subnet can't be discovered, so a startup
    discovery miss can't leave the destructive endpoints open to the guest for the
    process lifetime. Override with config ``guest_guard_fail_open=True``.
    """
    client_ip = _client_ip(request)
    if not vm_manager:
        return
    # FAIL CLOSED: ensure_vm_network() re-discovers if the init probe missed. If the subnet is
    # STILL unknown we can't prove the request isn't from the guest, so deny — a single startup
    # discovery miss must not leave the golden snapshot destroyable for the whole process lifetime.
    # Operators on odd topologies can opt back into fail-open via config.
    network_known = vm_manager.ensure_vm_network() is not None
    fail_open = bool(config and getattr(config, "guest_guard_fail_open", False))
    if not network_known and not fail_open:
        logger.error(
            "VM subnet unknown - denying destructive request from %s (fail-closed; set "
            "guest_guard_fail_open=True to override)",
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Guest network could not be determined; destructive operation denied (fail-closed)"
            ),
        )
    if vm_manager.is_from_vm(client_ip):
        logger.warning(f"Blocked destructive request from guest network: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation is not permitted from the guest network",
        )


def _require_from_vm(request: Request) -> None:
    """Accept heartbeats ONLY from the guest network.

    The heartbeat endpoint is what holds the auto-revert watchdog off. If ANY caller could post a
    heartbeat, an external host on the museum LAN (or a monitoring script accidentally left running)
    could keep the watchdog alive over a DEAD exhibit indefinitely — precisely the failure the
    watchdog exists to catch. Bind acceptance to the guest, which is the only legitimate heartbeat
    source (the guest's AutoIt/agent posts it, reaching us directly or via the bundled nginx whose
    X-Real-IP _client_ip honours).

    Fails CLOSED — rejects the heartbeat so the watchdog is free to revert — when the guest subnet
    can't be determined, unless guest_guard_fail_open=True (mirrors the destructive-endpoint guard).
    """
    if not vm_manager:
        return
    client_ip = _client_ip(request)
    network_known = vm_manager.ensure_vm_network() is not None
    fail_open = bool(config and getattr(config, "guest_guard_fail_open", False))
    if not network_known:
        if fail_open:
            return
        logger.error(
            "VM subnet unknown - rejecting heartbeat from %s (fail-closed; watchdog may revert). "
            "Set guest_guard_fail_open=True to override.",
            client_ip,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Guest network could not be determined; heartbeat rejected (fail-closed)",
        )
    if not vm_manager.is_from_vm(client_ip):
        logger.warning(f"Rejected heartbeat from non-guest source: {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Heartbeats are only accepted from the guest network",
        )


# Global state
vm_manager: Optional[VMManager] = None
heartbeat_monitor: Optional[HeartbeatMonitor] = None
config: Optional[Config] = None
plugin_registry: Optional[PluginRegistry] = None
config_path: Path = Path(os.environ.get("VMCTL_CONFIG", "config.yaml"))

# Tracks the deferred "enable heartbeat monitoring after boot delay" task so an operator
# /vm/stop can cancel it before it fires — otherwise a stop during the delay would be silently
# overridden by the pending enable and the intentionally-stopped exhibit would get restarted.
_delayed_enable_task: Optional[asyncio.Task] = None


def _cancel_delayed_enable() -> None:
    """Cancel any pending deferred heartbeat re-arm task."""
    global _delayed_enable_task
    if _delayed_enable_task is not None and not _delayed_enable_task.done():
        _delayed_enable_task.cancel()
    _delayed_enable_task = None


# Response Models
class StatusResponse(BaseModel):
    """VM and monitoring status."""

    vm_name: str
    vm_state: str
    vm_is_running: bool
    snapshot_name: str
    snapshot_exists: bool
    heartbeat: dict
    auto_revert_enabled: bool


class SnapshotInfo(BaseModel):
    """Snapshot information."""

    snapshot_name: str
    exists: bool


class SnapshotListResponse(BaseModel):
    """List of snapshots."""

    vm_name: str
    snapshots: list[str]


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
    details: Optional[dict] = None


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown events for the API.

    On startup:
    - Load configuration
    - Initialize VM manager
    - Initialize heartbeat monitor
    - Start monitoring loop

    On shutdown:
    - Stop monitoring loop
    """
    global vm_manager, heartbeat_monitor, config, plugin_registry

    # Startup
    logger.info("Starting Exhibition VM Controller API...")

    # Load config
    if config_path.exists():
        config = Config.from_yaml(config_path)
    else:
        logger.warning("config.yaml not found, using environment variables/defaults")
        try:
            config = Config()
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise

    config.configure_logging()
    logger.info(f"Configuration loaded: {config.get_summary()}")

    # Initialize heartbeat monitor with restart callback
    async def on_heartbeat_timeout():
        """Callback when heartbeat times out."""
        if vm_manager and vm_manager.auto_revert_enabled:
            logger.error("Heartbeat timeout - initiating VM restart")
            try:
                # Capture the desired-state token BEFORE the revert. An operator /vm/stop issued
                # DURING the revert bumps the generation (disable()); capturing here — not after
                # the revert — means the deferred re-arm below detects the mismatch and stands
                # down, so the intentional stop wins instead of being silently overridden by this
                # in-flight recovery.
                pre_revert_generation = heartbeat_monitor.generation if heartbeat_monitor else 0

                # Run synchronous VM restart in thread pool
                # Skip waiting for VM ready if QEMU agent checking is disabled
                loop = asyncio.get_running_loop()
                wait_for_ready = config.check_qemu_agent
                success = await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)

                # restart_vm(wait_for_ready=True) returns False when the guest never becomes QEMU-
                # agent ready. Re-arming here would declare the exhibit healthy while it is still
                # hung; instead leave the clock untouched so the monitor retries recovery on the
                # next cycle (throttled by recovery_backoff).
                if wait_for_ready and not success:
                    logger.error(
                        "VM did not become ready after auto-recovery restart - not re-arming; "
                        "recovery will be retried"
                    )
                    return

                logger.info("VM restarted successfully after heartbeat timeout")

                # Wait for VM to be ready, then re-ARM heartbeat monitoring. reset() ALONE leaves
                # last_heartbeat=None, and is_timed_out() returns False while None — so after an
                # auto-recovery a booted-but-silent guest (AutoIt heartbeat never comes back) would
                # NEVER be caught by the timeout path again. enable() restarts the timeout clock,
                # matching the manual /vm/restart path (reset → enable).
                #
                # Gate the re-enable on the generation token captured BEFORE the revert (above)
                # so an operator /vm/stop during either the revert or the boot delay is not
                # silently overridden.
                if heartbeat_monitor:
                    heartbeat_monitor.reset()
                    await asyncio.sleep(config.vm_startup_heartbeat_delay)
                    if heartbeat_monitor.enable_if_current(pre_revert_generation):
                        logger.info("Heartbeat monitoring re-armed")

            except Exception as e:
                logger.error(f"Failed to restart VM after timeout: {e}", exc_info=True)
        else:
            logger.warning(
                "Heartbeat timeout detected but auto-revert is disabled - "
                "manual intervention required"
            )

    # Initialize VM manager first (needed for heartbeat monitor)
    vm_manager = VMManager(
        vm_name=config.vm_name,
        snapshot_name=config.snapshot_name,
        auto_revert_enabled=config.auto_revert_enabled,
        on_reset_callback=None,  # Will be set after heartbeat monitor is created
        startup_wait_interval=config.vm_startup_wait_interval,
        startup_max_attempts=config.vm_startup_max_attempts,
        qemu_agent_timeout=config.qemu_agent_timeout,
    )

    # Initialize heartbeat monitor with VM state monitoring
    heartbeat_monitor = HeartbeatMonitor(
        timeout=config.heartbeat_timeout,
        check_interval=config.heartbeat_check_interval,
        on_timeout_callback=on_heartbeat_timeout,
        vm_manager=vm_manager,
        recovery_backoff=config.heartbeat_recovery_backoff,
    )

    # Set VM reset callback now that heartbeat monitor exists
    def on_vm_reset():
        """Callback when VM is reset."""
        if heartbeat_monitor:
            heartbeat_monitor.reset()

    vm_manager.on_reset_callback = on_vm_reset

    # Initialize plugin registry and load plugins
    plugin_registry = PluginRegistry(
        plugins_dir=Path("plugins"),
        hooks_dir=Path("hooks"),
    )
    plugin_registry.load_plugins(config.plugins)

    # Note: plugin web content is served via catch-all route at the bottom of api.py

    # Run plugin startup hooks
    for hook in plugin_registry.get_startup_hooks():
        if asyncio.iscoroutinefunction(hook):
            await hook(app)
        else:
            hook(app)

    logger.info("Plugin system initialized")

    # Ensure VM is running and reverted to clean state on startup
    if vm_manager.snapshot_exists():
        logger.info("Ensuring VM is in clean state on startup...")
        try:
            loop = asyncio.get_running_loop()
            wait_for_ready = config.check_qemu_agent
            success = await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)
            if wait_for_ready and not success:
                # The revert ran but the guest never became QEMU-agent ready. Don't crash startup
                # (a slow guest under Restart=always would crashloop) — surface it and let the
                # heartbeat monitor catch and recover a genuinely hung guest once armed.
                logger.error(
                    "VM did not become ready after startup revert - continuing; heartbeat "
                    "monitor will recover it if it stays hung"
                )
            else:
                logger.info("VM started and reverted to snapshot successfully")
        except Exception as e:
            logger.error(f"Failed to start VM on startup: {e}", exc_info=True)
            raise
    else:
        logger.warning(f"Snapshot '{config.snapshot_name}' not found — skipping startup revert")

    # Start heartbeat monitoring (delay enabling to give VM time to boot AutoIT scripts)
    await heartbeat_monitor.start_monitoring()

    async def _delayed_heartbeat_enable():
        # Capture the desired-state token now; if an operator /vm/stop bumps it during the sleep,
        # enable_if_current() refuses to arm and the stop stands. The task is also tracked so
        # /vm/stop can cancel it outright.
        generation = heartbeat_monitor.generation
        logger.info(
            f"Waiting {config.vm_startup_heartbeat_delay}s before enabling heartbeat monitoring..."
        )
        await asyncio.sleep(config.vm_startup_heartbeat_delay)
        if heartbeat_monitor.enable_if_current(generation):
            logger.info("Heartbeat monitoring enabled after startup delay")

    global _delayed_enable_task
    _delayed_enable_task = asyncio.create_task(_delayed_heartbeat_enable())

    logger.info("Exhibition VM Controller API started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Exhibition VM Controller API...")

    _cancel_delayed_enable()

    # Run plugin shutdown hooks
    if plugin_registry:
        for hook in plugin_registry.get_shutdown_hooks():
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(app)
                else:
                    hook(app)
            except Exception as e:
                logger.error(f"Error in shutdown hook: {e}", exc_info=True)

    if heartbeat_monitor:
        await heartbeat_monitor.stop_monitoring()

    logger.info("Exhibition VM Controller API shut down")


# Create FastAPI app
app = FastAPI(
    title="Exhibition VM Controller API",
    description="REST API for controlling VMs in exhibition environments",
    version="2.2.0-rc.1",
    lifespan=lifespan,
)

# Mount static files for web interface
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/ui", StaticFiles(directory=str(static_path), html=True), name="static")


# API Endpoints
@app.get("/api/v1/status", response_model=StatusResponse)
async def get_status():
    """Get current VM and monitoring status."""
    if not vm_manager or not heartbeat_monitor:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    try:
        return StatusResponse(
            vm_name=vm_manager.vm_name,
            vm_state=vm_manager.get_vm_state(),
            vm_is_running=vm_manager.is_running(),
            snapshot_name=vm_manager.snapshot_name,
            snapshot_exists=vm_manager.snapshot_exists(),
            heartbeat=heartbeat_monitor.get_status(),
            auto_revert_enabled=vm_manager.auto_revert_enabled,
        )
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting status: {str(e)}",
        )


@app.get("/api/v1/heartbeat", response_model=MessageResponse)
@app.post("/api/v1/heartbeat", response_model=MessageResponse)
async def receive_heartbeat(request: Request):
    """
    Receive heartbeat signal from VM guest.

    This endpoint should be called periodically by monitoring scripts
    running inside the VM to signal that the VM is alive and functioning.

    Only accepted from the guest network so an external caller can't hold the auto-revert
    watchdog open over a dead exhibit (see _require_from_vm).

    Supports both GET and POST methods for compatibility with AutoIt and other tools.
    """
    if not heartbeat_monitor:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Heartbeat monitor not initialized",
        )

    _require_from_vm(request)

    heartbeat_monitor.receive_heartbeat()

    if plugin_registry:
        plugin_registry.push_event("heartbeat", "{}")

    return MessageResponse(
        message="Heartbeat received",
        details=heartbeat_monitor.get_status(),
    )


@app.get("/api/v1/vm/start", response_model=MessageResponse)
@app.post("/api/v1/vm/start", response_model=MessageResponse)
async def start_vm(request: Request):
    """Start VM by reverting to snapshot. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    _deny_from_vm(request)

    was_enabled = heartbeat_monitor.enabled if heartbeat_monitor else False
    try:
        # Keep monitoring DISABLED across the revert. start_vm() briefly leaves the VM not-running
        # while it reverts to the snapshot; an armed monitor would see "VM not running" mid-revert
        # and fire a second, concurrent recovery. Mirror the /vm/restart path: disable → revert →
        # post-boot delay → re-arm (gated so an operator stop during the delay still wins).
        _cancel_delayed_enable()
        if heartbeat_monitor:
            heartbeat_monitor.disable()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vm_manager.start_vm)

        if heartbeat_monitor:
            heartbeat_monitor.reset()
            generation = heartbeat_monitor.generation
            await asyncio.sleep(config.vm_startup_heartbeat_delay)
            heartbeat_monitor.enable_if_current(generation)

        return MessageResponse(
            message=f"VM '{vm_manager.vm_name}' started successfully",
        )
    except Exception as e:
        logger.error(f"Error starting VM: {e}")
        # Restore prior monitoring state so a transient revert failure doesn't leave us blind.
        if heartbeat_monitor and was_enabled:
            heartbeat_monitor.reset()
            heartbeat_monitor.enable()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting VM: {str(e)}",
        )


@app.get("/api/v1/vm/stop", response_model=MessageResponse)
@app.post("/api/v1/vm/stop", response_model=MessageResponse)
async def stop_vm(request: Request):
    """Stop (destroy) VM. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    _deny_from_vm(request)

    was_enabled = heartbeat_monitor.enabled if heartbeat_monitor else False
    # Cancel any pending deferred re-arm and disable monitoring BEFORE the destroy. disable() also
    # bumps the generation token, so any re-arm already sleeping (post-recovery / post-boot) becomes
    # stale and won't restart the exhibit we are intentionally stopping.
    _cancel_delayed_enable()
    if heartbeat_monitor:
        heartbeat_monitor.disable()

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vm_manager.stop_vm)

        return MessageResponse(
            message=f"VM '{vm_manager.vm_name}' stopped successfully",
        )
    except Exception as e:
        logger.error(f"Error stopping VM: {e}")
        # The destroy failed — the VM is likely still running. Restore prior monitoring state so a
        # transient failure doesn't leave the controller permanently blind.
        if heartbeat_monitor and was_enabled:
            heartbeat_monitor.reset()
            heartbeat_monitor.enable()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error stopping VM: {str(e)}",
        )


@app.get("/api/v1/vm/restart", response_model=MessageResponse)
@app.post("/api/v1/vm/restart", response_model=MessageResponse)
async def restart_vm(request: Request):
    """Restart VM by reverting to snapshot. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    # Only apply pre-restart hooks for requests from inside the VM network.
    # Admin/system requests from outside are always allowed.
    client_ip = _client_ip(request)
    from_vm = vm_manager.is_from_vm(client_ip)
    if from_vm and plugin_registry:
        reason = plugin_registry.check_pre_restart()
        if reason:
            logger.info(f"Restart blocked for VM client {client_ip}: {reason}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )

    was_enabled = heartbeat_monitor.enabled if heartbeat_monitor else False
    try:
        _cancel_delayed_enable()
        if heartbeat_monitor:
            heartbeat_monitor.disable()

        loop = asyncio.get_running_loop()
        wait_for_ready = config.check_qemu_agent
        success = await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)

        # restart_vm(wait_for_ready=True) returns False when the guest never becomes QEMU-agent
        # ready. Treat that as a failure: skip post-restart hooks and the re-arm, restore prior
        # monitoring state so we aren't blind, and surface a 500 rather than reporting success.
        if wait_for_ready and not success:
            logger.error(f"VM '{vm_manager.vm_name}' did not become ready after restart")
            if heartbeat_monitor and was_enabled:
                heartbeat_monitor.reset()
                heartbeat_monitor.enable()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="VM restart did not reach a ready state",
            )

        # Run post-restart hooks
        if plugin_registry:
            plugin_registry.run_post_restart()

        if heartbeat_monitor:
            heartbeat_monitor.reset()
            # Wait for VM to boot before re-enabling; gate the re-arm on the generation token so an
            # operator /vm/stop during the boot delay is not silently overridden.
            generation = heartbeat_monitor.generation
            await asyncio.sleep(config.vm_startup_heartbeat_delay)
            heartbeat_monitor.enable_if_current(generation)

        return MessageResponse(
            message=f"VM '{vm_manager.vm_name}' restarted successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error restarting VM: {e}")
        # Restore prior monitoring state so a transient revert failure doesn't leave us blind.
        if heartbeat_monitor and was_enabled:
            heartbeat_monitor.reset()
            heartbeat_monitor.enable()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error restarting VM: {str(e)}",
        )


@app.get("/api/v1/snapshots", response_model=SnapshotListResponse)
async def list_snapshots():
    """List all snapshots for the VM."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    try:
        loop = asyncio.get_running_loop()
        snapshots = await loop.run_in_executor(None, vm_manager.list_snapshots)

        return SnapshotListResponse(
            vm_name=vm_manager.vm_name,
            snapshots=snapshots,
        )
    except Exception as e:
        logger.error(f"Error listing snapshots: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error listing snapshots: {str(e)}",
        )


@app.get("/api/v1/snapshot/create", response_model=MessageResponse)
@app.post("/api/v1/snapshot/create", response_model=MessageResponse)
async def create_snapshot(request: Request, snapshot_name: Optional[str] = None):
    """
    Create a new snapshot (default: create/update the 'ready' snapshot).

    Query parameter:
    - snapshot_name: Name for the snapshot (optional, default: configured snapshot_name)

    Supports both GET and POST methods for compatibility with AutoIt and other tools.
    """
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    _deny_from_vm(request)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vm_manager.create_snapshot, snapshot_name)

        name = snapshot_name or vm_manager.snapshot_name
        return MessageResponse(
            message=f"Snapshot '{name}' created successfully for VM '{vm_manager.vm_name}'",
        )
    except Exception as e:
        logger.error(f"Error creating snapshot: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating snapshot: {str(e)}",
        )


@app.get("/api/v1/snapshot/delete/{snapshot_name}", response_model=MessageResponse)
@app.delete("/api/v1/snapshot/{snapshot_name}", response_model=MessageResponse)
async def delete_snapshot(request: Request, snapshot_name: str):
    """
    Delete a snapshot.

    Supports both GET (at /api/v1/snapshot/delete/{name}) and DELETE (at
    /api/v1/snapshot/{name}).
    """
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    _deny_from_vm(request)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, vm_manager.delete_snapshot, snapshot_name)

        return MessageResponse(
            message=f"Snapshot '{snapshot_name}' deleted successfully",
        )
    except Exception as e:
        logger.error(f"Error deleting snapshot: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting snapshot: {str(e)}",
        )


@app.get("/api/v1/revert/enable", response_model=MessageResponse)
@app.post("/api/v1/revert/enable", response_model=MessageResponse)
async def enable_auto_revert(request: Request):
    """Enable automatic revert on heartbeat timeout. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    _deny_from_vm(request)
    vm_manager.enable_auto_revert()

    return MessageResponse(
        message="Automatic revert enabled",
    )


@app.get("/api/v1/revert/disable", response_model=MessageResponse)
@app.post("/api/v1/revert/disable", response_model=MessageResponse)
async def disable_auto_revert(request: Request):
    """Disable automatic revert (for maintenance). Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    _deny_from_vm(request)
    vm_manager.disable_auto_revert()

    return MessageResponse(
        message="Automatic revert disabled - manual intervention required on failures",
    )


@app.get("/api/v1/heartbeat/status", response_model=dict)
async def get_heartbeat_status():
    """Get detailed heartbeat monitoring status."""
    if not heartbeat_monitor:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Heartbeat monitor not initialized",
        )

    return heartbeat_monitor.get_status()


# ==============================================================================
# Plugin System Endpoints
# ==============================================================================


@app.get("/api/v1/state")
async def get_plugin_state():
    """Aggregated state from all plugins."""
    if not plugin_registry:
        return JSONResponse({})
    return JSONResponse(plugin_registry.get_state())


@app.get("/api/v1/events")
async def sse_events(request: Request):
    """Server-Sent Events stream. Plugins push events into this."""
    if not plugin_registry:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin system not initialized",
        )

    queue = plugin_registry.sse_connect()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"event: {event}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            plugin_registry.sse_disconnect(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/v1/poll/{resource}")
async def poll_resource(resource: str):
    """Poll current state of a resource. Returns plain text for AutoIt compatibility."""
    if not plugin_registry:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin system not initialized"
        )
    return PlainTextResponse(content=plugin_registry.get_poll_value(resource))


@app.get("/api/v1/signal/{event}")
async def signal_event(event: str, value: str = ""):
    """Receive signal from guest (fire-and-forget). Dispatches to plugin handlers."""
    if not plugin_registry:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin system not initialized"
        )
    plugin_registry.handle_signal(event, value)
    return PlainTextResponse(content="ok")


@app.get("/api/v1/poll/{resource}/set")
@app.post("/api/v1/poll/{resource}/set")
async def set_poll_value(resource: str, value: str):
    """Set poll value for external systems (e.g., Arduino controllers)."""
    if not plugin_registry:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin system not initialized"
        )
    plugin_registry.set_poll_value(resource, value)
    return MessageResponse(message=f"Poll value '{resource}' set to '{value}'")


# ==============================================================================
# Plugin Web Content (catch-all — must be last)
# ==============================================================================


@app.get("/{path:path}")
async def serve_plugin_content(path: str):
    """Serve plugin web content. Falls through to 404 if no plugin handles it."""
    if plugin_registry:
        response = plugin_registry.serve_web(path)
        if response is not None:
            return response
    raise HTTPException(status_code=404, detail="Not found")


def main():
    """Main entry point for running the API server."""
    global config_path

    parser = argparse.ArgumentParser(description="Exhibition VM Controller API")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config YAML file (default: config.yaml)",
    )
    args = parser.parse_args()

    # Set env var so uvicorn's module re-import picks up the right config
    os.environ["VMCTL_CONFIG"] = str(args.config)
    config_path = args.config

    if config_path.exists():
        cfg = Config.from_yaml(config_path)
    else:
        cfg = Config()

    cfg.configure_logging()

    logger.info(f"Starting Exhibition VM Controller API on {cfg.api_host}:{cfg.api_port}")

    uvicorn.run(
        "vm_controller.api:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=cfg.api_reload,
        log_level=cfg.log_level.lower(),
    )


if __name__ == "__main__":
    main()
