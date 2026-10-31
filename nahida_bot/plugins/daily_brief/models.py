"""Local-time briefing settings, persisted per authenticated account."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BriefPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    time: str = "08:00"
    timezone: str = "Asia/Shanghai"
    catchup_minutes: int = Field(default=240, ge=1, le=720)

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        parsed = time.fromisoformat(value)
        if len(value) != 5 or parsed.tzinfo is not None:
            raise ValueError("Use HH:MM in the configured timezone")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    def window(self, now: datetime) -> tuple[datetime, datetime]:
        local = now.astimezone(ZoneInfo(self.timezone))
        due = datetime.combine(
            local.date(), time.fromisoformat(self.time), local.tzinfo
        )
        end_of_day = datetime.combine(
            local.date() + timedelta(days=1), time.min, local.tzinfo
        )
        return due, min(due + timedelta(minutes=self.catchup_minutes), end_of_day)
