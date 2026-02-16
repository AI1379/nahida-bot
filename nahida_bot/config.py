"""
Pydantic-based configuration management for Nahida Bot.

This module provides a centralized configuration system that supports:
- YAML configuration files
- Environment variables (loaded by nonebot's dotenv)
- Pydantic validation and type checking

Configuration hierarchy:
- CoreConfig: Core bot settings (host, port, logging, etc.)
- OpenAIConfig: OpenAI plugin configuration
- PixivConfig: Pixiv plugin configuration
- AppConfig: Main application configuration combining all above
"""

from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv
from nonebot.log import logger
import yaml
import os


class CoreConfig(BaseModel):
    """Core bot configuration."""

    host: str = Field(default="127.0.0.1", description="Host to bind to")
    port: int = Field(default=5700, description="Port to bind to")
    log_level: str = Field(default="INFO", description="Log level")
    log_file: Optional[str] = Field(default=None, description="Log file path")
    data_dir: str = Field(default="data", description="Data directory path")
    command_start: list[str] = Field(
        default=["!", "/"], description="Command start tokens"
    )
    command_sep: list[str] = Field(
        default=["."], description="Command separator tokens"
    )
    superusers: list[str] = Field(default_factory=list, description="Superuser IDs")

    def __init__(self, **data):
        """Initialize config with default values for list fields."""
        if "command_start" not in data or data["command_start"] is None:
            data["command_start"] = ["/", "!"]
        if "command_sep" not in data or data["command_sep"] is None:
            data["command_sep"] = ["."]
        super().__init__(**data)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v.upper()

    @field_validator("data_dir")
    @classmethod
    def validate_data_dir(cls, v: str) -> str:
        """Ensure data_dir is a valid path."""
        if not v:
            return "data"
        return v


class OpenAIConfig(BaseModel):
    """OpenAI plugin configuration."""

    api_url: str = Field(
        default="https://api.siliconflow.cn/v1", description="OpenAI API base URL"
    )
    api_token: str = Field(default="", description="OpenAI API token")
    model_name: str = Field(
        default="Pro/deepseek-ai/DeepSeek-V3", description="OpenAI model name"
    )
    default_prompt: Optional[str] = Field(
        default=None, description="Default system prompt"
    )
    message_timeout: int = Field(
        default=7 * 24 * 3600,
        description="Message timeout in seconds (default: 7 days)",
    )
    max_memory: int = Field(default=50, description="Maximum context messages to keep")


class PixivConfig(BaseModel):
    """Pixiv plugin configuration."""

    refresh_tokens: list[str] = Field(
        default_factory=list, description="List of Pixiv refresh tokens"
    )


class AppConfig(BaseModel):
    """Main application configuration combining all components."""

    core: CoreConfig = Field(
        default_factory=CoreConfig, description="Core bot settings"
    )
    openai: Optional[OpenAIConfig] = Field(
        default=None, description="OpenAI plugin settings"
    )
    pixiv: Optional[PixivConfig] = Field(
        default=None, description="Pixiv plugin settings"
    )

    plugins: list[str] = Field(
        default_factory=list,
        description="List of plugins to load (empty for all)",
    )

    model_config = {"extra": "allow"}  # Allow extra config fields for compatibility


def merge_pydantic_models[T: BaseModel](base: T, override: BaseModel) -> T:
    """Merge two Pydantic models, with override taking precedence."""
    base_dict = base.model_dump()
    override_dict = override.model_dump()
    merged_dict = {**base_dict, **override_dict}
    return base.__class__(**merged_dict)


def load_config(config_file: Optional[str] = None) -> AppConfig:
    """
    Load configuration from YAML file and environment variables.

    Priority (highest to lowest):
    1. Environment variables (loaded by nonebot's dotenv)
    2. YAML configuration file
    3. Default values in config models

    Args:
        config_file: Path to YAML configuration file.
                    If None, defaults to "config.yaml"

    Returns:
        AppConfig instance with merged configuration
    """

    if config_file is None:
        config_file = "config.yaml"

    current_env = os.getenv("ENVIRONMENT", "dev").lower()
    load_dotenv()

    # Load environment-specific .env file if it exists
    load_dotenv(f".env.{current_env}")

    if not Path(config_file).exists():
        raise FileNotFoundError(f"Configuration file '{config_file}' not found.")

    with open(config_file, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f) or {}

    def resolve_list_str(data: str) -> str | list:
        if isinstance(data, str) and data.startswith("[") and data.endswith("]"):
            try:
                result = yaml.safe_load(data)
                logger.debug(f"Parsed list from string: {result}")
                return result
            except yaml.YAMLError:
                logger.warning(f"Failed to parse list from string: {data}")
                return data
        return data

    # Check all ${ENV_VAR} in yaml_data and load from environment variables
    def resolve_env_vars(data):
        if isinstance(data, dict):
            return {k: resolve_env_vars(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [resolve_env_vars(i) for i in data]
        elif isinstance(data, str) and data.startswith("${") and data.endswith("}"):
            env_var = data[2:-1]
            env = resolve_list_str(os.getenv(env_var, ""))
            logger.debug(f"Resolving environment variable '{env_var}': {env}")
            logger.debug(f"Environment type: {type(env)}")
            return env
        else:
            return data

    yaml_data = resolve_env_vars(yaml_data)

    yaml_config = AppConfig.model_validate(yaml_data)

    return yaml_config


# Global config instance
config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """
    Get the global config instance.

    Returns:
        AppConfig instance

    Raises:
        RuntimeError: If config has not been initialized
    """
    global config
    if config is None:
        raise RuntimeError("Config not initialized. Call init_config() first.")
    return config


def init_config(config_file: Optional[str] = None) -> AppConfig:
    """
    Initialize the global config instance.

    Args:
        config_file: Path to YAML configuration file

    Returns:
        The initialized AppConfig instance
    """
    global config
    config = load_config(config_file)
    return config
