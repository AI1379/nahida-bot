"""Scheduler data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True, frozen=True)
class CronJob:
    """A scheduled cron job persisted in SQLite."""

    job_id: str
    platform: str
    chat_id: str
    session_key: str  # typed: "{platform}:{target_type}:{chat_id}"
    prompt: str
    mode: Literal["once", "interval", "cron"]
    fire_at: str | None  # ISO8601 UTC for "once"
    interval_seconds: int | None  # for "interval"
    cron_expression: str | None  # standard 5-field cron for "cron"
    max_runs: int | None  # None = infinite
    run_count: int
    is_active: bool
    created_at: str
    next_fire_at: str
    last_fired_at: str | None
    workspace_id: str | None
    claimed_at: str | None = None
    failure_count: int = 0
    last_error: str | None = None
    session_mode: Literal["main", "isolated", "fresh", "named"] = "main"
    session_name: str | None = None  # required when session_mode="named"
    chat_type: str = ""  # "private", "group", etc.
    created_by_user_id: str = ""
    created_from_session_id: str = ""
    created_from_chat_address: str = ""
    sender_account_key: str = ""


@dataclass(slots=True, frozen=True)
class SchedulerConfig:
    """Configuration for the SchedulerService."""

    poll_interval_seconds: float = 1.0
    max_concurrent_fires: int = 5
    job_timeout_seconds: float = 120.0
    min_interval_seconds: int = 60
    max_prompt_chars: int = 12000
    max_jobs_per_chat: int = 20
    failure_retry_seconds: int = 300
    max_consecutive_failures: int = 3
    memory_dreaming_enabled: bool = True
    memory_dreaming_interval_seconds: int = 3600
    memory_dreaming_initial_delay_seconds: int = 300
    memory_dreaming_session_limit: int = 20
    memory_dreaming_recent_turn_limit: int = 40
    memory_dreaming_model: str = ""
