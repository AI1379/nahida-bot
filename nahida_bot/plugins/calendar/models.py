"""Explicit calendar time ranges and revision-bound personal-event commands."""

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def calendar_time(value: str) -> date | datetime:
    if len(value) == 10:
        return date.fromisoformat(value)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Event timestamps must include an explicit UTC offset")
    return result


def validate_span(start: str, end: str) -> None:
    a, b = calendar_time(start), calendar_time(end)
    if type(a) is not type(b) or b <= a:
        raise ValueError(
            "Use matching date-only or offset timestamps; end must be after start"
        )


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def valid_window(self) -> "TimeWindow":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Query timestamps must include UTC offsets")
        if not timedelta(0) < self.end - self.start <= timedelta(days=90):
            raise ValueError("Query range must be positive and at most 90 days")
        return self


class AvailabilityRequest(TimeWindow):
    duration_minutes: int = Field(default=60, ge=5, le=1440)


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    start: str = Field(
        description="ISO date or datetime with UTC offset. End date is exclusive for all-day events."
    )
    end: str
    description: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=300)
    busy: bool = True
    allow_conflicts: bool = False
    task_id: str = Field(default="", max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_times(self) -> "EventCreate":
        validate_span(self.start, self.end)
        return self


class EventMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_id: str = Field(pattern=r"^evt_[0-9a-f]{32}$")
    expected_revision: str = Field(min_length=1, max_length=512)
    operation_id: str = Field(min_length=1, max_length=128)


class EventUpdate(EventMutation):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start: str | None = None
    end: str | None = None
    description: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=300)
    busy: bool | None = None
    allow_conflicts: bool = False

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None:
            calendar_time(value)
        return value


def tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    if "operation_id" in schema.get("properties", {}):
        schema["required"] = [
            key for key in schema.get("required", []) if key != "operation_id"
        ]
        schema["properties"]["operation_id"]["description"] = (
            "Optional retry key; reuse for the same action, omit for a new action."
        )
    return schema
