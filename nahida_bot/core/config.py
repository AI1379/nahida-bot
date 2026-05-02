"""Application configuration."""

import os
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "openai-compatible"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    models: list[str] = []


class ProviderEntryConfig(BaseModel):
    """One provider entry in the multi-provider dict."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "openai-compatible"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    models: list[str] = []


class Settings(BaseModel):
    """Main application settings."""

    model_config = ConfigDict(frozen=True, extra="allow")

    # Application
    app_name: str = "Nahida Bot"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool | None = None

    # Server
    host: str = "127.0.0.1"
    port: int = 6185

    # Database
    db_path: str = "./data/nahida.db"

    # Workspace
    workspace_base_dir: str = "./data/workspace"

    # Plugins
    plugin_paths: list[str] = ["./plugins"]
    discover_builtin_channels: bool = True

    # Agent / Router
    system_prompt: str = "You are a helpful assistant."

    # Provider (LLM backend) — single provider (legacy)
    provider: ProviderConfig = ProviderConfig()

    # Multi-provider (new). Dict keyed by provider id.
    # If non-empty, takes priority over the single `provider` field.
    providers: dict[str, ProviderEntryConfig] = {}
    default_provider: str = ""


def _interpolate_env(value: Any, env_map: dict[str, str | None]) -> Any:
    """Recursively interpolate ``${VAR}`` and ``${VAR:default}`` in config values."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            inner = value[2:-1]
            parts = inner.split(":", 1)
            env_var = parts[0]
            default = parts[1] if len(parts) > 1 else None
            resolved = env_map.get(env_var, os.environ.get(env_var, default))
            return resolved
        return value
    if isinstance(value, dict):
        return {k: _interpolate_env(v, env_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v, env_map) for v in value]
    return value


def load_settings(
    config_yaml: str | None = None,
    env_path: str | None = None,
    **kwargs: Any,
) -> Settings:
    """Load application settings."""
    if config_yaml:
        with open(config_yaml, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
    else:
        yaml_config = {}

    env_path_in_env = os.environ.get("ENV_PATH")
    if env_path_in_env:
        env_path = env_path_in_env

    env_config: dict[str, str | None] = {}
    if env_path:
        env_config = dict(dotenv_values(env_path))

    # Build env lookup: .env values take precedence over os.environ
    env_map = dict(os.environ) | env_config

    # Recursively interpolate ${VAR} and ${VAR:default} in all config values
    yaml_config = _interpolate_env(yaml_config, env_map)

    full_config = yaml_config | env_config | kwargs

    # Specially update log level if debug is True and log_level is not explicitly set
    if full_config.get("debug") and "log_level" not in kwargs:
        full_config["log_level"] = "DEBUG"

    return Settings(**full_config)
