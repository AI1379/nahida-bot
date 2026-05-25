"""Pydantic request and response schemas for the WebAPI."""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# -- Health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    app_name: str
    started: bool


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


class SessionHistoryResponse(BaseModel):
    session_id: str
    turns: list[TurnResponse]


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
    session_mode: Literal["main", "isolated", "named"] = "main"
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
