"""Application configuration."""

import os

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict


# TODO: Implement the Setting parsing logic manually, instead of use pydantic-settings.
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

    # Plugins
    plugin_paths: list[str] = ["./plugins"]


def load_settings(
    config_yaml: str | None = None,
    env_path: str | None = None,
    **kwargs,
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
    if env_path:
        env_config = dotenv_values(env_path)
    else:
        env_config = {}

    # Parse "${ENV_VAR}" syntax in YAML config values
    for key, value in yaml_config.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            extract_default = value[2:-1].split(":", 1)
            env_var = extract_default[0]
            default_value = extract_default[1] if len(extract_default) > 1 else None
            yaml_config[key] = env_config.get(
                env_var, os.environ.get(env_var, default_value)
            )

    full_config = yaml_config | env_config | kwargs

    # Specially update log level if debug is True and log_level is not explicitly set
    if full_config.get("debug") and "log_level" not in kwargs:
        full_config["log_level"] = "DEBUG"

    return Settings(**full_config)
