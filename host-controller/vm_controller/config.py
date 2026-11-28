"""
Configuration management for Exhibition VM Controller.

Author: Marc Schütze
Organization: ZKM | Center for Art and Media Karlsruhe
Contact: mschuetze@zkm.de
License: MIT

This module provides configuration management using Pydantic, supporting
YAML files, environment variables, and validation.
"""

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Config(BaseSettings):
    """
    Configuration for Exhibition VM Controller.

    Settings can be loaded from:
    1. config.yaml file
    2. Environment variables (with VMCTL_ prefix)
    3. Default values

    Environment variables take precedence over config file.
    """

    model_config = SettingsConfigDict(
        env_prefix="VMCTL_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # VM Configuration
    vm_name: str = Field(description="Name of the VM in libvirt")

    backend: Literal["libvirt", "process"] = Field(
        default="libvirt",
        description="'process' runs an emulator from process_dir instead of a libvirt domain",
    )

    process_dir: str = Field(
        default="",
        description="Instance directory holding process.json; required for backend 'process'",
    )

    snapshot_name: str = Field(default="ready", description="Name of the snapshot to revert to")

    # Heartbeat Configuration
    heartbeat_timeout: float = Field(
        default=10.0,
        description="Seconds without heartbeat before considering VM failed",
        gt=0,
    )

    heartbeat_check_interval: float = Field(
        default=0.5,
        description="Seconds between heartbeat timeout checks",
        gt=0,
    )

    heartbeat_recovery_backoff: float = Field(
        default=30.0,
        description=(
            "Minimum seconds between recovery attempts. Edge-triggers recovery so a VM that "
            "stays down (or auto-revert being disabled) can't re-fire recovery every check "
            "interval and flood the journal/disk."
        ),
        gt=0,
    )

    # VM Startup Configuration
    vm_startup_wait_interval: float = Field(
        default=10.0,
        description="Seconds between VM responsiveness checks during startup",
        gt=0,
    )

    vm_startup_max_attempts: int = Field(
        default=30,
        description="Maximum attempts to check VM responsiveness during startup",
        gt=0,
    )

    vm_startup_heartbeat_delay: float = Field(
        default=10.0,
        description="Seconds to wait after VM responsive before enabling heartbeat",
        gt=0,
    )

    # Auto-Revert Configuration
    auto_revert_enabled: bool = Field(
        default=True, description="Enable automatic revert on heartbeat timeout"
    )

    guest_guard_fail_open: bool = Field(
        default=False,
        description=(
            "If the VM subnet can't be discovered, whether the guest-origin guard on destructive "
            "endpoints (snapshot delete/revert, stop) should FAIL OPEN (allow) instead of the safe "
            "default of FAIL CLOSED (deny). Leave False in production: fail-open lets stray art "
            "content reach those endpoints when discovery is broken. Only enable on odd topologies "
            "where the guest genuinely can't reach the API and operator lockout would be worse."
        ),
    )

    trusted_proxies: list[str] = Field(
        default=["127.0.0.1", "::1"],
        description=(
            "IPs of reverse proxies allowed to set the real client address via X-Real-IP / "
            "X-Forwarded-For. When a request's direct peer is in this list, the guest-origin "
            "guard uses the forwarded IP instead of the proxy's. The bundled nginx proxies from "
            "127.0.0.1, so that is trusted by default; set to [] for a pure direct-bind setup."
        ),
    )

    # API Configuration
    api_host: str = Field(default="0.0.0.0", description="Host to bind API server to")

    api_port: int = Field(
        default=8000,
        description="Port for API server",
        gt=0,
        lt=65536,
    )

    api_reload: bool = Field(default=False, description="Enable auto-reload for development")

    # Logging Configuration
    # Literal, not str: a typo used to surface as an AttributeError in configure_logging() and
    # crash-loop the unit; now it is one validation message at load.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )

    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, v: object) -> object:
        # Configs written before the Literal used lowercase ("info"); keep accepting them.
        return v.upper() if isinstance(v, str) else v

    # Plugin Configuration
    plugins: list[str] | None = Field(
        default=None,
        description=(
            "List of plugin names to load (without .py). If not set, all plugins are loaded."
        ),
    )

    # QEMU Guest Agent Configuration
    check_qemu_agent: bool = Field(
        default=True, description="Verify VM responsiveness using QEMU guest agent"
    )

    qemu_agent_timeout: float = Field(
        default=5.0,
        description="Timeout for QEMU guest agent commands",
        gt=0,
    )

    @classmethod
    def from_yaml(cls, config_path: Path) -> "Config":
        """
        Load configuration from YAML file.

        Args:
            config_path: Path to config.yaml file

        Returns:
            Config instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid
        """
        logger.info(f"Loading configuration from {config_path}")

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path) as f:
            config_data = yaml.safe_load(f)

        if config_data is None:
            config_data = {}

        # A top-level list/scalar (e.g. a YAML document that is a bare list) would make
        # ``cls(**config_data)`` raise a raw TypeError and crashloop the process under
        # Restart=always. Fail with a clear, actionable message instead.
        if not isinstance(config_data, dict):
            raise ValueError(
                f"Config file {config_path} must contain a top-level mapping (key: value pairs), "
                f"got {type(config_data).__name__}"
            )

        return cls(**config_data)

    def configure_logging(self) -> None:
        """Configure Python logging based on config."""
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper()),
            format=self.log_format,
        )

        # Set specific loggers
        logging.getLogger("vm_controller").setLevel(self.log_level.upper())
        logging.getLogger("uvicorn").setLevel("INFO")

    def get_summary(self) -> dict:
        """
        Get a summary of key configuration values.

        Returns:
            Dictionary with main config values
        """
        return {
            "vm_name": self.vm_name,
            "snapshot_name": self.snapshot_name,
            "heartbeat_timeout": self.heartbeat_timeout,
            "auto_revert_enabled": self.auto_revert_enabled,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "check_qemu_agent": self.check_qemu_agent,
        }
