"""Application configuration."""

import os
from pathlib import Path
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
    tags: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)


ProviderModelEntry = str | ProviderModelConfig


class ProviderQuotaConfig(BaseModel):
    """Optional provider-owned balance or subscription quota settings."""

    model_config = ConfigDict(frozen=True, extra="allow")

    adapter: str = ""
    url: str = ""
    api_key: str = ""
    team: bool = False
    organization_id: str = ""
    project_id: str = ""
    windows: list[dict[str, Any]] = Field(default_factory=list)


class ProviderEntryConfig(BaseModel):
    """One provider entry in the multi-provider dict."""

    model_config = ConfigDict(frozen=True, extra="allow")

    type: str = "openai-compatible"
    api_key: str = ""
    base_url: str = ""
    models: list[ProviderModelEntry] = Field(default_factory=list)
    quota: ProviderQuotaConfig | None = None


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

    # A small default steps to prevent unexpected long-running loops.
    max_steps: int = Field(default=8, ge=1)
    provider_timeout_seconds: float = Field(default=120.0, ge=0)
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


class AgentRuntimeConfig(BaseModel):
    """Agent runtime / canonical-ledger configuration (agent-loop repair).

    Phase 1 adds the canonical run ledger (best-effort dual-write of run
    events + execution receipts). It is purely additive — no user-facing
    behaviour change — so the flag only controls whether the ledger is
    written. Default off in code; enabled in this repo's config.yaml to
    collect data. Later phases add receipt verification, channel progress,
    and legacy-history mode under the same block.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    canonical_ledger_enabled: bool = False

    transcript_replay_enabled: bool = False


ReasoningPolicyValue = Literal["strip", "append", "budget"]


class ContextConfig(BaseModel):
    """Context window budget configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    max_tokens: int = Field(default=272000, ge=1)
    reserved_tokens: int = Field(default=10000, ge=0)
    max_chars: int | None = None
    reserved_chars: int = Field(default=0, ge=0)
    # Larger summaries help preserve more dropped context on modern long-window models.
    summary_max_chars: int = Field(default=2000, ge=0)
    reasoning_policy: ReasoningPolicyValue = "budget"
    max_reasoning_tokens: int = Field(default=2000, ge=0)


class SchedulerConfigModel(BaseModel):
    """Scheduler service configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    poll_interval_seconds: float = Field(default=1.0, ge=0.1)
    max_concurrent_fires: int = Field(default=5, ge=1)
    job_timeout_seconds: float = Field(default=120.0, ge=1)
    min_interval_seconds: int = Field(default=60, ge=1)
    max_prompt_chars: int = Field(default=12000, ge=1)
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


class NodeProtocolConfigModel(BaseModel):
    """Gateway-Node protocol configuration (WebSocket node layer)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    heartbeat_interval_ms: int = Field(default=15000, ge=1000)
    heartbeat_timeout_ms: int = Field(default=45000, ge=2000)
    pairing_ttl_seconds: int = Field(default=600, ge=10)
    node_token_ttl_seconds: int = Field(default=0, ge=0, description="0 = no expiry")


class WebApiSpeechConfigModel(BaseModel):
    """Speech pipeline configuration for the WebAPI / Desktop TTS path.

    Independent from the ``plugins.tts`` config: users can run the speak
    plugin (Channel ``/speak``) without exposing the REST surface, or vice
    versa. To share one GPT-SoVITS instance across both, use a YAML anchor::

        webapi:
          speech: &tts
            backends:
              default: {type: gpt-sovits-v2, base_url: "http://127.0.0.1:9880"}
            voices:
                nahida: {ref_audio_path: "/data/nahida.wav", prompt_text: "…"}
        plugins:
          tts:
            enabled: true
            config: *tts
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    # Raw backend / voice dicts; SpeechService parses them per provider type.
    backends: dict[str, dict[str, Any]] = Field(default_factory=dict)
    voices: dict[str, dict[str, Any]] = Field(default_factory=dict)
    default_backend: str = "default"
    default_voice: str = ""
    artifact_cache_dir: str = "./data/speech_cache"
    artifact_ttl_seconds: int = Field(default=6 * 60 * 60, ge=60)
    artifact_max_bytes: int = Field(default=256 * 1024 * 1024, ge=16 * 1024 * 1024)
    max_text_length: int = Field(default=500, ge=1)
    max_concurrency: int = Field(default=1, ge=1, le=16)


class WebAPIConfigModel(BaseModel):
    """WebAPI service configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    auth_token: str = ""
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    host: str = ""
    port: int = 0
    nodes: NodeProtocolConfigModel = NodeProtocolConfigModel()
    speech: WebApiSpeechConfigModel = WebApiSpeechConfigModel()


class WebUIAuthConfigModel(BaseModel):
    """Browser WebUI authentication configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    admin_password: str = ""
    admin_password_hash: str = ""
    session_ttl_seconds: int = Field(default=3600, ge=60)
    login_rate_per_minute: int = Field(default=5, ge=0)
    bind_session_to_ip: bool = True


class WebUIConfigModel(BaseModel):
    """Browser WebUI configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    auth: WebUIAuthConfigModel = WebUIAuthConfigModel()


class MemoryRetrievalConfig(BaseModel):
    """Durable memory retrieval configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    fts_enabled: bool = True
    vector_enabled: bool = False
    hybrid_enabled: bool = True
    max_injected_items: int = Field(default=5, ge=0)
    max_injected_chars: int = Field(default=4000, ge=0)
    vector_backend: Literal["json", "sqlite-vec", "none"] = "json"
    # Soft-scope recall (Piece A2). When on, the memory cascade fills any
    # remaining budget with a global pass that admits ONLY ``sensitivity=
    # 'public'`` items from outside the current scope cascade — restricted
    # items never enter the result set (SQL-level ``sensitivity='public'``).
    # Default off: zero behavior change until backfill (A1) has softened
    # legacy ``private`` items to ``public``.
    soft_scope: bool = False


class MemoryEmbeddingConfig(BaseModel):
    """Durable memory embedding configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    model: str = ""
    provider_id: str = ""  # Legacy: prefer ``model: provider/model``.
    dimensions: int = Field(default=0, ge=0)
    batch_size: int = Field(default=16, ge=1)
    embed_after_consolidation: bool = True


class MemoryConsolidationConfig(BaseModel):
    """Durable memory consolidation configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    rule_based_enabled: bool = True


class MemoryConfig(BaseModel):
    """Memory subsystem configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    retrieval: MemoryRetrievalConfig = MemoryRetrievalConfig()
    embedding: MemoryEmbeddingConfig = MemoryEmbeddingConfig()
    consolidation: MemoryConsolidationConfig = MemoryConsolidationConfig()


class KBAutoRecallConfig(BaseModel):
    """Lightweight KB auto-recall configuration (§3.1 trigger).

    When enabled, a small FTS search runs across all KB collections before
    each agent turn and injects top results as context — like Memory does,
    but with stricter defaults (fewer items, fewer chars, FTS-only).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    max_items: int = Field(default=2, ge=0)
    max_chars: int = Field(default=2000, ge=0)
    # Off by default: FTS BM25 scores are *negative* (more negative = better),
    # so a finite magnitude threshold is mode-dependent and fragile — the
    # per-collection top-1 + cross-collection merge already picks the best
    # hits. Set a finite value only if you understand the BM25 scale (a value
    # like -10 would otherwise *drop* the strongest matches, since they score
    # below -10). float('-inf') disables filtering.
    min_score: float = Field(default=float("-inf"))


class GroupContextConfig(BaseModel):
    """Observed group-chat context injection configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    max_messages: int = Field(default=20, ge=0)
    ttl_seconds: int = Field(default=900, ge=0)
    max_chars: int = Field(default=4000, ge=0)
    # Short ambient-message gap used to delimit the current group topic.
    # Unlike continuity_gap_seconds (bot dialogue), this scans group user
    # messages including observed-only turns. 0 disables topic segmentation.
    topic_gap_seconds: int = Field(default=300, ge=0)
    # Dialogue continuity gate for group chats (issue #37). When the gap
    # between the current trigger and the bot's previous dialogue turn
    # exceeds this threshold, the old dialogue is treated as a stale,
    # separate conversation and dropped from history (only the observed
    # context window is retained). 0 disables the gate. Group-only;
    # private chats always keep full history.
    continuity_gap_seconds: int = Field(default=1800, ge=0)


class IdentityAccountSeed(BaseModel):
    """A platform account to seed-link to a person at startup."""

    model_config = ConfigDict(frozen=True, extra="allow")

    channel: str
    account_type: str = "user"
    platform_account_id: str
    label: str = ""


class IdentityPersonSeed(BaseModel):
    """A person and their accounts, seeded from config (verification=config_seed)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    person_id: str
    display_name: str = ""
    accounts: list[IdentityAccountSeed] = Field(default_factory=list)


class IdentityAuthorizationTicketsConfig(BaseModel):
    """Short-lived, one-use delegation tickets approved by declared admins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    challenge_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    grant_ttl_seconds: int = Field(default=300, ge=30, le=900)
    max_grant_ttl_seconds: int = Field(default=900, ge=30, le=3600)


class IdentityConfig(BaseModel):
    """Person/account identity system (issue #7).

    Disabled by default: with ``enabled=False`` the resolver is a no-op and
    ``SessionContext`` carries empty identity fields, so existing behavior is
    unchanged. Seeded links are upserted (never deleted) at startup.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    people: list[IdentityPersonSeed] = Field(default_factory=list)
    # Phase A action-authorization (see docs/design/memory-soft-scope-and-authz.md).
    # Account keys authorized for privileged tools (exec/message/workspace_write/
    # management). Decoupled from ``people``: declaring someone a Person does NOT
    # make them an admin. Only consulted when ``enabled`` is true.
    admins: list[IdentityAccountSeed] = Field(default_factory=list)
    authorization_tickets: IdentityAuthorizationTicketsConfig = (
        IdentityAuthorizationTicketsConfig()
    )


class RouterConfigModel(BaseModel):
    """Message router configuration."""

    model_config = ConfigDict(frozen=True, extra="allow")

    system_prompt: str = "You are a helpful assistant."
    max_history_turns: int = Field(default=200, ge=1)
    agent_enabled: bool = True
    command_timeout_seconds: float = Field(default=30.0, ge=0)
    command_timeout_message: str = "Command timed out. Please try again later."
    reply_to_inbound: bool = True
    show_reasoning: bool = False
    # TODO: Check if 2000 chars is too small for reasoning traces.
    reasoning_max_chars: int = Field(default=2000, ge=0)
    enable_silent_reply: bool = True
    group_context: GroupContextConfig = GroupContextConfig()


class MotionPlannerConfigModel(BaseModel):
    """Server-side motion planning for Desktop DisplayPlan generation.

    When enabled, the agent pipeline calls a cheap LLM model to analyze the
    reply text and produce emotion/motion/voice tags per sentence. The result
    is attached to ``OutboundMessage.extra["display_plan"]`` and forwarded to
    the Desktop node via ``agent.message.completed`` events.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = False
    model_tag: str = Field(
        default="cheap", description="Model tag resolved via ModelRouter"
    )
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=60.0)


RestartPolicy = Literal["no", "on-failure", "always"]
HealthCheckType = Literal["tcp_port", "none"]


class ProcessHealthCheckConfig(BaseModel):
    """Health probe for a supervised process.

    Phase 1 only supports ``tcp_port`` (open a TCP connection to ``host:port``
    and treat success as healthy). ``none`` disables probing entirely.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    type: HealthCheckType = "none"
    host: str = "127.0.0.1"
    port: int = Field(default=0, ge=0, le=65535)
    interval_seconds: float = Field(default=15.0, ge=0.1)
    timeout_seconds: float = Field(default=3.0, ge=0.1)
    unhealthy_after: int = Field(default=3, ge=1)
    start_period_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Grace period after start during which failures are not counted.",
    )


class ProcessSpec(BaseModel):
    """One supervised process declaration.

    ``command`` is executed via the shell when ``shell`` is true, otherwise it
    is treated as the executable name and ``args`` as its argv list. Processes
    do NOT inherit the bot process environment; only a small whitelist
    (``PATH``, ``SYSTEMROOT`` on Windows, etc.) plus the user-declared ``env``
    is passed down.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    command: str
    args: list[str] = Field(default_factory=list)
    shell: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    working_dir: str | None = None
    restart_policy: RestartPolicy | None = None
    health_check: ProcessHealthCheckConfig = Field(
        default_factory=ProcessHealthCheckConfig
    )
    depends_on: list[str] = Field(default_factory=list)
    shutdown_timeout_seconds: float | None = None
    startup_wait_seconds: float | None = None


class ProcessDefaultsConfig(BaseModel):
    """Supervisor-level defaults applied to every spec unless overridden.

    Per-spec overrides live directly on :class:`ProcessSpec`
    (``restart_policy``, ``shutdown_timeout_seconds``, ``startup_wait_seconds``).
    Backoff and circuit-breaker knobs have no per-spec counterpart because they
    govern the restart *loop*, not the process itself.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    restart_policy: RestartPolicy = "on-failure"
    backoff_initial_seconds: float = Field(default=1.0, ge=0.0)
    backoff_max_seconds: float = Field(default=60.0, ge=0.1)
    backoff_factor: float = Field(default=2.0, ge=1.0)
    restart_max_attempts: int = Field(
        default=0, ge=0, description="0 = unlimited; >0 trips circuit breaker"
    )
    restart_window_seconds: float = Field(
        default=300.0,
        ge=1.0,
        description="Sliding window for counting restart attempts",
    )
    shutdown_timeout_seconds: float = Field(default=10.0, ge=1.0)
    startup_wait_seconds: float = Field(default=0.0, ge=0.0)
    log_buffer_lines: int = Field(default=1000, ge=10)


class ProcessSupervisorConfig(BaseModel):
    """Top-level ``processes:`` configuration block.

    ``defaults`` apply to every spec; per-spec fields override them. Specs are
    keyed by process name (``[a-z0-9_-]+``), which must be unique across both
    config-declared and plugin-contributed processes.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool = True
    defaults: ProcessDefaultsConfig = Field(default_factory=ProcessDefaultsConfig)
    specs: dict[str, ProcessSpec] = Field(default_factory=dict)


class Settings(BaseModel):
    """Main application settings."""

    model_config = ConfigDict(frozen=True, extra="allow")

    # Application
    app_name: str = "Nahida Bot"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool | None = None
    log_file: str | None = None
    log_file_level: str | None = None
    log_file_json: bool = True
    log_file_max_bytes: int = Field(default=10485760, ge=0)  # 10 MB; 0 = no rotation
    log_file_backup_count: int = Field(default=5, ge=0)  # 0 = keep no backups
    dependency_log_level: str = "WARNING"
    logger_levels: dict[str, str] = Field(default_factory=dict)

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
    enable_silent_reply: bool = True

    # LLM providers. Dict keyed by provider id.
    providers: dict[str, ProviderEntryConfig] = {}
    default_provider: str = ""

    # Multimodal context
    multimodal: MultimodalConfig = MultimodalConfig()

    # Internal subsystem configs
    agent: AgentConfig = AgentConfig()
    agent_runtime: AgentRuntimeConfig = AgentRuntimeConfig()
    context: ContextConfig = ContextConfig()
    scheduler: SchedulerConfigModel = SchedulerConfigModel()
    router: RouterConfigModel = RouterConfigModel()
    webapi: WebAPIConfigModel = WebAPIConfigModel()
    webui: WebUIConfigModel = WebUIConfigModel()
    model_routing: dict[str, Any] = Field(default_factory=dict)  # Legacy, ignored.
    motion_planner: MotionPlannerConfigModel = MotionPlannerConfigModel()
    memory: MemoryConfig = MemoryConfig()
    kb_auto_recall: KBAutoRecallConfig = KBAutoRecallConfig()
    identity: IdentityConfig = IdentityConfig()
    processes: ProcessSupervisorConfig = ProcessSupervisorConfig()


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


DEFAULT_CONFIG_YAML = "config.yaml"
DEFAULT_ENV_PATH = ".env"


def find_config_yaml(explicit: str | None = None) -> str | None:
    """Resolve the YAML config path for a CLI run.

    Priority: explicit argument > ``NAHIDA_CONFIG`` env var > ``./config.yaml``
    if present in the current working directory. Returns None when nothing is
    found so callers can fall back to pure defaults.
    """
    if explicit:
        return explicit
    env_cfg = os.environ.get("NAHIDA_CONFIG")
    if env_cfg:
        return env_cfg
    if Path(DEFAULT_CONFIG_YAML).is_file():
        return DEFAULT_CONFIG_YAML
    return None


def find_env_path(explicit: str | None = None) -> str | None:
    """Resolve the ``.env`` path for a CLI run.

    Priority: explicit argument > ``ENV_PATH`` env var > ``./.env`` if present.
    Returns None when nothing is found.
    """
    if explicit:
        return explicit
    env_path = os.environ.get("ENV_PATH")
    if env_path:
        return env_path
    if Path(DEFAULT_ENV_PATH).is_file():
        return DEFAULT_ENV_PATH
    return None


def load_settings(
    config_yaml: str | None = None,
    env_path: str | None = None,
    **kwargs: Any,
) -> Settings:
    """Load application settings.

    Note: this function is *pure* — it does not auto-discover files. CLI
    entry points should resolve paths via :func:`find_config_yaml` and
    :func:`find_env_path` first so that ``./config.yaml`` and ``./.env`` are
    picked up automatically while keeping programmatic callers hermetic.
    """
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


def load_settings_auto(
    config_yaml: str | None = None,
    env_path: str | None = None,
    **kwargs: Any,
) -> Settings:
    """Load settings with CLI-style auto-discovery of config.yaml and .env.

    Thin wrapper around :func:`load_settings` that resolves paths via
    :func:`find_config_yaml` and :func:`find_env_path` first. Intended for CLI
    entry points; programmatic callers should use :func:`load_settings` directly
    to stay hermetic.
    """
    return load_settings(
        config_yaml=find_config_yaml(config_yaml),
        env_path=find_env_path(env_path),
        **kwargs,
    )
