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

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vm_controller.config import Config
from vm_controller.heartbeat_monitor import HeartbeatMonitor
from vm_controller.vm_manager import VMManager

logger = logging.getLogger(__name__)

# Global state
vm_manager: Optional[VMManager] = None
heartbeat_monitor: Optional[HeartbeatMonitor] = None
config: Optional[Config] = None


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
    global vm_manager, heartbeat_monitor, config

    # Startup
    logger.info("Starting Exhibition VM Controller API...")

    # Load config
    config_path = Path("config.yaml")
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

    # Ensure VM is running and reverted to clean state on startup
    logger.info("Ensuring VM is in clean state on startup...")
    try:
        loop = asyncio.get_event_loop()
        wait_for_ready = config.check_qemu_agent
        await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)
        logger.info("VM started and reverted to snapshot successfully")
    except Exception as e:
        logger.error(f"Failed to start VM on startup: {e}", exc_info=True)
        raise

    # Start heartbeat monitoring
    await heartbeat_monitor.start_monitoring()
    heartbeat_monitor.enable()

    logger.info("Exhibition VM Controller API started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Exhibition VM Controller API...")

    if heartbeat_monitor:
        await heartbeat_monitor.stop_monitoring()

    logger.info("Exhibition VM Controller API shut down")


# Create FastAPI app
app = FastAPI(
    title="Exhibition VM Controller API",
    description="REST API for controlling VMs in exhibition environments",
    version="1.1.0",
    lifespan=lifespan,
)


# API Endpoints
@app.get("/", response_model=MessageResponse)
async def root():
    """Root endpoint with API information."""
    return MessageResponse(
        message="Exhibition VM Controller API",
        details={
            "version": "1.1.0",
            "documentation": "/docs",
            "status": "/api/v1/status",
        },
    )


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
        # Disable heartbeat monitoring while stopping
        if heartbeat_monitor:
            heartbeat_monitor.disable()

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
async def restart_vm():
    """Restart VM by reverting to snapshot. Supports both GET and POST methods."""
    if not vm_manager:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VM manager not initialized",
        )

    try:
        if heartbeat_monitor:
            heartbeat_monitor.disable()

        loop = asyncio.get_event_loop()
        wait_for_ready = config.check_qemu_agent
        success = await loop.run_in_executor(None, vm_manager.restart_vm, wait_for_ready)

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


def main():
    """Main entry point for running the API server."""
    # Load config for uvicorn settings
    config_path = Path("config.yaml")
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
