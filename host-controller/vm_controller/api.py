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

# Global state
vm_manager: Optional[VMManager] = None
heartbeat_monitor: Optional[HeartbeatMonitor] = None
config: Optional[Config] = None
plugin_registry: Optional[PluginRegistry] = None
config_path: Path = Path(os.environ.get("VMCTL_CONFIG", "config.yaml"))


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
                # Run synchronous VM restart in thread pool
                # Skip waiting for VM ready if QEMU agent checking is disabled
                loop = asyncio.get_event_loop()
                wait_for_ready = config.check_qemu_agent
                await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)
                logger.info("VM restarted successfully after heartbeat timeout")

                # Wait for VM to be ready, then re-enable heartbeat monitoring
                await asyncio.sleep(config.vm_startup_heartbeat_delay)
                if heartbeat_monitor:
                    heartbeat_monitor.enable()
                    logger.info("Heartbeat monitoring re-enabled")

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
    )

    # Initialize heartbeat monitor with VM state monitoring
    heartbeat_monitor = HeartbeatMonitor(
        timeout=config.heartbeat_timeout,
        check_interval=config.heartbeat_check_interval,
        on_timeout_callback=on_heartbeat_timeout,
        vm_manager=vm_manager,
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

    # Include plugin routers
    for router in plugin_registry.get_routers():
        app.include_router(router)

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
            loop = asyncio.get_event_loop()
            wait_for_ready = config.check_qemu_agent
            await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)
            logger.info("VM started and reverted to snapshot successfully")
        except Exception as e:
            logger.error(f"Failed to start VM on startup: {e}", exc_info=True)
            raise
    else:
        logger.warning(f"Snapshot '{config.snapshot_name}' not found — skipping startup revert")

    # Start heartbeat monitoring (delay enabling to give VM time to boot AutoIT scripts)
    await heartbeat_monitor.start_monitoring()

    async def _delayed_heartbeat_enable():
        logger.info(f"Waiting {config.vm_startup_heartbeat_delay}s before enabling heartbeat monitoring...")
        await asyncio.sleep(config.vm_startup_heartbeat_delay)
        heartbeat_monitor.enable()
        logger.info("Heartbeat monitoring enabled after startup delay")

    asyncio.create_task(_delayed_heartbeat_enable())

    logger.info("Exhibition VM Controller API started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Exhibition VM Controller API...")

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
    version="2.0.0",
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
async def receive_heartbeat():
    """
    Receive heartbeat signal from VM guest.

    This endpoint should be called periodically by monitoring scripts
    running inside the VM to signal that the VM is alive and functioning.

    Supports both GET and POST methods for compatibility with AutoIt and other tools.
    """
    if not heartbeat_monitor:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Heartbeat monitor not initialized",
        )

    heartbeat_monitor.receive_heartbeat()

    return MessageResponse(
        message="Heartbeat received",
        details=heartbeat_monitor.get_status(),
    )


@app.get("/api/v1/vm/start", response_model=MessageResponse)
@app.post("/api/v1/vm/start", response_model=MessageResponse)
async def start_vm():
    """Start VM by reverting to snapshot. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    try:
        # Clear manual stop flag to re-enable auto-restart
        if heartbeat_monitor:
            heartbeat_monitor.clear_manual_stop()

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, vm_manager.start_vm)

        return MessageResponse(
            message=f"VM '{vm_manager.vm_name}' started successfully",
        )
    except Exception as e:
        logger.error(f"Error starting VM: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting VM: {str(e)}",
        )


@app.get("/api/v1/vm/stop", response_model=MessageResponse)
@app.post("/api/v1/vm/stop", response_model=MessageResponse)
async def stop_vm():
    """Stop (destroy) VM. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    try:
        # Mark as manual stop to prevent auto-restart
        if heartbeat_monitor:
            heartbeat_monitor.set_manual_stop()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, vm_manager.stop_vm)

        return MessageResponse(
            message=f"VM '{vm_manager.vm_name}' stopped successfully",
        )
    except Exception as e:
        logger.error(f"Error stopping VM: {e}")
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
    client_ip = request.client.host if request.client else ""
    from_vm = vm_manager.is_from_vm(client_ip)
    if from_vm and plugin_registry:
        reason = plugin_registry.check_pre_restart()
        if reason:
            logger.info(f"Restart blocked for VM client {client_ip}: {reason}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )

    try:
        if heartbeat_monitor:
            heartbeat_monitor.clear_manual_stop()
            heartbeat_monitor.disable()

        loop = asyncio.get_event_loop()
        wait_for_ready = config.check_qemu_agent
        success = await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)

        # Run post-restart hooks
        if plugin_registry:
            plugin_registry.run_post_restart()

        if success and heartbeat_monitor:
            # Wait before re-enabling heartbeat
            await asyncio.sleep(config.vm_startup_heartbeat_delay)
            heartbeat_monitor.enable()

        return MessageResponse(
            message=f"VM '{vm_manager.vm_name}' restarted successfully",
        )
    except Exception as e:
        logger.error(f"Error restarting VM: {e}")
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
        loop = asyncio.get_event_loop()
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
async def create_snapshot(snapshot_name: Optional[str] = None):
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

    try:
        loop = asyncio.get_event_loop()
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
async def delete_snapshot(snapshot_name: str):
    """
    Delete a snapshot.

    Supports both GET (at /api/v1/snapshot/delete/{name}) and DELETE (at /api/v1/snapshot/{name}) methods.
    """
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    try:
        loop = asyncio.get_event_loop()
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
async def enable_auto_revert():
    """Enable automatic revert on heartbeat timeout. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    vm_manager.enable_auto_revert()

    return MessageResponse(
        message="Automatic revert enabled",
    )


@app.get("/api/v1/revert/disable", response_model=MessageResponse)
@app.post("/api/v1/revert/disable", response_model=MessageResponse)
async def disable_auto_revert():
    """Disable automatic revert (for maintenance). Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/poll/{resource}")
async def poll_resource(resource: str):
    """Poll current state of a resource. Returns plain text for AutoIt compatibility."""
    if not plugin_registry:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin system not initialized")
    return PlainTextResponse(content=plugin_registry.get_poll_value(resource))


@app.get("/api/v1/signal/{event}")
async def signal_event(event: str, value: str = ""):
    """Receive signal from guest (fire-and-forget). Dispatches to plugin handlers."""
    if not plugin_registry:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin system not initialized")
    plugin_registry.handle_signal(event, value)
    return PlainTextResponse(content="ok")


@app.get("/api/v1/poll/{resource}/set")
@app.post("/api/v1/poll/{resource}/set")
async def set_poll_value(resource: str, value: str):
    """Set poll value for external systems (e.g., Arduino controllers)."""
    if not plugin_registry:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin system not initialized")
    plugin_registry.set_poll_value(resource, value)
    return MessageResponse(message=f"Poll value '{resource}' set to '{value}'")


def main():
    """Main entry point for running the API server."""
    global config_path

    parser = argparse.ArgumentParser(description="Exhibition VM Controller API")
    parser.add_argument(
        "--config", "-c",
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
