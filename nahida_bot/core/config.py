"""Application configuration."""

import os
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

ImageFallbackMode = Literal["auto", "tool", "off"]
MediaContextPolicy = Literal["cache_aware", "description_only", "native_recent"]


class ProviderModelConfig(BaseModel):
    """One model entry under a provider."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    capabilities: dict[str, Any] = Field(default_factory=dict)


ProviderModelEntry = str | ProviderModelConfig


class ProviderEntryConfig(BaseModel):
    """One provider entry in the multi-provider dict."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "openai-compatible"
    api_key: str = ""
    base_url: str = ""
    models: list[ProviderModelEntry] = Field(default_factory=list)


class MultimodalConfig(BaseModel):
    """Multimodal context configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    image_fallback_mode: ImageFallbackMode = "auto"
    media_context_policy: MediaContextPolicy = "cache_aware"
    image_fallback_provider: str = ""
    image_fallback_model: str = ""
    max_images_per_turn: int = Field(default=4, ge=0)
    max_image_bytes: int = Field(default=10485760, ge=0)  # 10 MB
    media_cache_ttl_seconds: int = Field(default=3600, ge=0)


class AgentConfig(BaseModel):
    """Agent loop configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    max_steps: int = Field(default=8, ge=1)
    provider_timeout_seconds: float = Field(default=30.0, ge=0)
    retry_attempts: int = Field(default=2, ge=0)
    retry_backoff_seconds: float = Field(default=0.2, ge=0)
    tool_timeout_seconds: float = Field(default=135.0, ge=0)
    tool_retry_attempts: int = Field(default=1, ge=0)
    tool_retry_backoff_seconds: float = Field(default=0.1, ge=0)
    max_tool_log_chars: int = Field(default=400, ge=0)
    tool_use_system_prompt: str = (
        "Tool use policy: When a tool is needed, call it through the structured "
        "tool/function calling interface. Do not merely say that you will call a "
        "tool. After tool results are provided, continue reasoning from the "
        "results and produce the final user-facing answer."
    )
    provider_error_template: str = (
        "Service temporarily unavailable ({code}). Please try again later."
    )


ReasoningPolicyValue = Literal["strip", "append", "budget"]


class ContextConfig(BaseModel):
    """Context window budget configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    max_tokens: int = Field(default=8000, ge=1)
    reserved_tokens: int = Field(default=1000, ge=0)
    max_chars: int | None = None
    reserved_chars: int = Field(default=0, ge=0)
    summary_max_chars: int = Field(default=600, ge=0)
    reasoning_policy: ReasoningPolicyValue = "budget"
    max_reasoning_tokens: int = Field(default=2000, ge=0)


class SchedulerConfigModel(BaseModel):
    """Scheduler service configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    poll_interval_seconds: float = Field(default=1.0, ge=0.1)
    max_concurrent_fires: int = Field(default=5, ge=1)
    job_timeout_seconds: float = Field(default=120.0, ge=1)
    min_interval_seconds: int = Field(default=60, ge=1)
    max_prompt_chars: int = Field(default=4000, ge=1)
    max_jobs_per_chat: int = Field(default=20, ge=1)
    failure_retry_seconds: int = Field(default=300, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)
    memory_dreaming_enabled: bool = True
    memory_dreaming_interval_seconds: int = Field(default=3600, ge=60)
    memory_dreaming_initial_delay_seconds: int = Field(default=300, ge=0)
    memory_dreaming_session_limit: int = Field(default=20, ge=1)
    memory_dreaming_recent_turn_limit: int = Field(default=40, ge=2)
    memory_dreaming_provider_id: str = ""
    memory_dreaming_model: str = ""


class RouterConfigModel(BaseModel):
    """Message router configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    system_prompt: str = "You are a helpful assistant."
    max_history_turns: int = Field(default=50, ge=1)
    agent_enabled: bool = True
    command_timeout_seconds: float = Field(default=30.0, ge=0)
    command_timeout_message: str = "Command timed out. Please try again later."


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

    # LLM providers. Dict keyed by provider id.
    providers: dict[str, ProviderEntryConfig] = {}
    default_provider: str = ""

    # Multimodal context
    multimodal: MultimodalConfig = MultimodalConfig()

    # Internal subsystem configs
    agent: AgentConfig = AgentConfig()
    context: ContextConfig = ContextConfig()
    scheduler: SchedulerConfigModel = SchedulerConfigModel()
    router: RouterConfigModel = RouterConfigModel()


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
