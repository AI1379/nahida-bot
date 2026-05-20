"""Pydantic request and response schemas for the WebAPI."""

from pydantic import BaseModel, Field


# -- Health ---------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    app_name: str
    started: bool


# -- Sessions -------------------------------------------------------------


class SessionSummaryResponse(BaseModel):
    session_id: str
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
    platform: str
    chat_id: str
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


class CronListResponse(BaseModel):
    jobs: list[CronJobResponse]


class CreateCronRequest(BaseModel):
    platform: str
    chat_id: str
    prompt: str
    mode: str
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None
    session_mode: str = "main"


class CreateCronResponse(BaseModel):
    job_id: str
    status: str


class UpdateCronRequest(BaseModel):
    prompt: str | None = None
    mode: str | None = None
    fire_at: str | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    max_runs: int | None = None


class CronActionResponse(BaseModel):
    job_id: str
    status: str
