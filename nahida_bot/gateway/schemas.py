"""Pydantic request and response schemas for the WebAPI."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# -- Health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    app_name: str
    started: bool


# -- Status ---------------------------------------------------------------


class StatusResponse(BaseModel):
    app: dict[str, Any]
    resources: dict[str, Any]
    services: dict[str, Any]
    usage: dict[str, Any]


# -- Bootstrap ------------------------------------------------------------


class BootstrapResponse(BaseModel):
    app_name: str
    version: str
    api_base: str
    webui_base: str
    auth: dict[str, Any]
    features: list[dict[str, str]]
    server_time: str


# -- Auth -----------------------------------------------------------------


class AuthLoginRequest(BaseModel):
    password: str


class AuthLoginResponse(BaseModel):
    authenticated: bool
    mode: str = "password"
    expires_at: str = ""


class AuthSessionResponse(BaseModel):
    authenticated: bool
    auth_required: bool
    mode: str
    expires_at: str = ""


# -- System Actions -------------------------------------------------------


class SystemActionRequest(BaseModel):
    confirm: bool = False
    reason: str = ""


class SystemActionResponse(BaseModel):
    accepted: bool
    action: str
    mode: str
    message: str


# -- Plugins --------------------------------------------------------------


class PluginSummaryResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    state: str
    configured_enabled: bool = True
    path: str
    entrypoint: str
    load_phase: str
    nahida_bot_version: str = ""
    sdk_version: str = ""
    error_message: str = ""
    permissions: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[dict[str, str]] = Field(default_factory=list)
    config_keys: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    has_config: bool = False
    has_instance: bool = False
    has_runtime_api: bool = False


class PluginListResponse(BaseModel):
    plugins: list[PluginSummaryResponse]


class PluginActionResponse(BaseModel):
    plugin_id: str
    action: str
    state: str
    status: str


# -- Config ---------------------------------------------------------------


class ConfigValueResponse(BaseModel):
    path: str
    type: str
    value: str


class ConfigCurrentResponse(BaseModel):
    content: str
    checksum: str
    path: str
    mtime: str
    entries: list[ConfigValueResponse] = Field(default_factory=list)


class ConfigDocumentResponse(BaseModel):
    content: str
    checksum: str
    path: str
    mtime: str
    data: dict[str, Any] = Field(default_factory=dict)
    redacted_data: dict[str, Any] = Field(default_factory=dict)
    redacted_paths: list[str] = Field(default_factory=list)
    entries: list[ConfigValueResponse] = Field(default_factory=list)


class ConfigSchemaResponse(BaseModel):
    entries: list[dict[str, str]]


class ConfigValidateResponse(BaseModel):
    errors: int
    warnings: int
    ok: bool
    issues: list[dict[str, str]]


class ConfigSaveRequest(BaseModel):
    content: str
    expected_checksum: str
    format: str = "yaml"


class ConfigPatchChange(BaseModel):
    path: str
    value: Any = None
    remove: bool = False
    secret_action: Literal["keep", "replace", "clear"] | None = None


class ConfigPatchRequest(BaseModel):
    expected_checksum: str
    changes: list[ConfigPatchChange] = Field(default_factory=list)


class ConfigSaveResponse(BaseModel):
    saved: bool
    backup_path: str | None = None
    checksum: str = ""
    restart_required: bool = True
    validation: dict[str, Any]


class ConfigRestoreRequest(BaseModel):
    expected_checksum: str | None = None


# -- Sessions -------------------------------------------------------------


class SessionSummaryResponse(BaseModel):
    session_id: str
    session_key_kind: str = ""
    workspace_id: str | None
    created_at: str
    last_active_at: str
    turn_count: int
    metadata: dict = Field(default_factory=dict)


class SessionListResponse(BaseModel):
    sessions: list[SessionSummaryResponse]


class TurnResponse(BaseModel):
    turn_id: int
    role: str
    content: str
    source: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    sentinel_action: str | None = None
    sentinel_suppressed: bool = False


class SessionHistoryResponse(BaseModel):
    session_id: str
    turns: list[TurnResponse]


class MessageDeliveryResponse(BaseModel):
    delivery_id: str
    target_chat_address: str
    platform: str
    target_type: str
    target_id: str
    source_session_id: str = ""
    source_chat_address: str = ""
    source_user_id: str = ""
    source: str = ""
    delivery_mode: str = ""
    status: str = ""
    message_id: str = ""
    text: str = ""
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    sentinel_action: str | None = None
    sentinel_suppressed: bool = False


class MessageDeliveryGroupResponse(BaseModel):
    target_chat_address: str
    platform: str
    target_type: str
    target_id: str
    count: int
    last_created_at: str
    last_source: str = ""


class MessageDeliveryGroupsResponse(BaseModel):
    groups: list[MessageDeliveryGroupResponse]


class MessageDeliveriesResponse(BaseModel):
    target_chat_address: str
    deliveries: list[MessageDeliveryResponse]


class SessionSearchResultResponse(BaseModel):
    result_type: Literal["turn", "delivery"]
    id: str
    session_id: str = ""
    target_chat_address: str = ""
    role: str = ""
    source: str = ""
    content: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    sentinel_action: str | None = None
    sentinel_suppressed: bool = False
    delivery_mode: str = ""
    status: str = ""
    message_id: str = ""


class SessionSearchResponse(BaseModel):
    results: list[SessionSearchResultResponse]


# -- Send Message ---------------------------------------------------------


class SendMessageRequest(BaseModel):
    target: str = ""
    text: str
    session_id: str | None = None


class SendMessageResponse(BaseModel):
    status: str
    session_id: str


# -- Cron -----------------------------------------------------------------


class CronJobResponse(BaseModel):
    job_id: str
    platform: str
    chat_id: str
    mode: str
    prompt: str
    is_active: bool
    next_fire_at: str | None
    run_count: int
    created_at: str
    session_mode: str = "main"
    session_name: str | None = None
    # Extended fields for WebUI
    session_key: str = ""
    chat_type: str = ""
    last_fired_at: str | None = None
    failure_count: int = 0
    last_error: str | None = None
    claimed_at: str | None = None
    workspace_id: str | None = None
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None
    created_by_user_id: str = ""
    created_from_session_id: str = ""
    created_from_chat_address: str = ""
    sender_account_key: str = ""


class CronListResponse(BaseModel):
    jobs: list[CronJobResponse]


class CreateCronRequest(BaseModel):
    target: str = ""
    prompt: str
    mode: Literal["once", "interval", "cron"]
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None
    session_mode: Literal["main", "isolated", "fresh", "named"] = "main"
    session_name: str | None = None

    @model_validator(mode="after")
    def _validate_session(self) -> "CreateCronRequest":
        if self.session_mode == "named":
            if not self.session_name:
                raise ValueError(
                    "session_name is required when session_mode is 'named'"
                )
            if not re.match(r"^[a-zA-Z0-9_-]+$", self.session_name):
                raise ValueError(
                    "session_name must contain only letters, digits, hyphens, and underscores"
                )
        elif self.session_name:
            raise ValueError("session_name is only valid when session_mode is 'named'")
        return self


class CreateCronResponse(BaseModel):
    job_id: str
    status: str


class UpdateCronRequest(BaseModel):
    prompt: str | None = None
    mode: Literal["once", "interval", "cron"] | None = None
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None
    session_mode: Literal["main", "isolated", "fresh", "named"] | None = None
    session_name: str | None = None


class CronActionResponse(BaseModel):
    job_id: str
    status: str


# -- Logs -----------------------------------------------------------------


class LogEntry(BaseModel):
    timestamp: str = ""
    level: str = ""
    logger: str = ""
    event: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


class LogsResponse(BaseModel):
    entries: list[LogEntry]


# -- Token Usage ----------------------------------------------------------


class TokenTotalsSchema(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost: float | None = None
    event_count: int = 0


class ProviderTokenSchema(BaseModel):
    provider_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated: bool = False
    estimated_cost: float | None = None
    event_count: int = 0


class DailyTokenSchema(BaseModel):
    date: str  # "YYYY-MM-DD"
    input_tokens: int = 0
    output_tokens: int = 0
    provider_id: str = ""
    model: str = ""


class TokenStatsResponse(BaseModel):
    totals: TokenTotalsSchema
    by_provider: list[ProviderTokenSchema]
    daily: list[DailyTokenSchema]


class TokenEventSchema(BaseModel):
    id: int | None = None
    timestamp: str = ""
    session_id: str = ""
    source_tag: str = ""
    provider_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated: bool = False
    estimated_cost: float | None = None


class TokenEventsResponse(BaseModel):
    events: list[TokenEventSchema]


class TokenClearResponse(BaseModel):
    cleared: bool


# -- Processes ---------------------------------------------------------------


class ProcessInfoResponse(BaseModel):
    name: str
    owner: str
    status: str
    pid: int | None = None
    restart_count: int = 0
    exit_code: int | None = None
    started_at: str | None = None
    last_error: str | None = None
    health: str = "unknown"
    restart_policy: str = "on-failure"
    command: str = ""


class ProcessListResponse(BaseModel):
    processes: list[ProcessInfoResponse]


class ProcessLogsResponse(BaseModel):
    name: str
    stdout: list[str]
    stderr: list[str]


class ProcessActionResponse(BaseModel):
    name: str
    status: str
    ok: bool = True
