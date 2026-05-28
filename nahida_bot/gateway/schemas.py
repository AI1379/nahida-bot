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


# -- Config ---------------------------------------------------------------


class ConfigCurrentResponse(BaseModel):
    content: str
    checksum: str
    path: str
    mtime: str


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


class ConfigSaveResponse(BaseModel):
    saved: bool
    backup_path: str | None = None
    checksum: str = ""
    restart_required: bool = True
    validation: dict[str, Any]


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
    next_fire_at: str
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
