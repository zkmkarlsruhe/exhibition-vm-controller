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
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml

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
    vm_name: str = Field(
        description="Name of the VM in libvirt"
    )

    snapshot_name: str = Field(
        default="ready",
        description="Name of the snapshot to revert to"
    )

    # Heartbeat Configuration
    heartbeat_timeout: float = Field(
        default=15.0,
        description="Seconds without heartbeat before considering VM failed",
        gt=0,
    )

    heartbeat_check_interval: float = Field(
        default=0.5,
        description="Seconds between heartbeat timeout checks",
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
        default=True,
        description="Enable automatic revert on heartbeat timeout"
    )

    # API Configuration
    api_host: str = Field(
        default="0.0.0.0",
        description="Host to bind API server to"
    )

    api_port: int = Field(
        default=8000,
        description="Port for API server",
        gt=0,
        lt=65536,
    )

    api_reload: bool = Field(
        default=False,
        description="Enable auto-reload for development"
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )

    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format"
    )

    # QEMU Guest Agent Configuration
    check_qemu_agent: bool = Field(
        default=True,
        description="Verify VM responsiveness using QEMU guest agent"
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

        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)

        if config_data is None:
            config_data = {}

        return cls(**config_data)

    def save_yaml(self, config_path: Path) -> None:
        """
        Save configuration to YAML file.

        Args:
            config_path: Path to save config.yaml
        """
        logger.info(f"Saving configuration to {config_path}")

        config_dict = self.model_dump()

        with open(config_path, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

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
