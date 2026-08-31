"""Configuration for SAIA MCP server."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SAIA MCP server settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    saia_api_key: str = ""
    saia_base_url: str = "https://chat-ai.academiccloud.de/v1"
    saia_audio_base_url: str = "https://saia.gwdg.de/v1"
    docs_url: str = "https://docs.hpc.gwdg.de/services/saia/"
    model_cache_ttl: int = 3600
    default_max_tokens: int = 1024
    default_temperature: float = 0.7
    request_timeout: int = 120


settings = Settings()
