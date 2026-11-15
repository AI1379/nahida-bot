"""Task data and commands, independent of notification and UI implementations."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from zoneinfo import ZoneInfo


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    planned_on: date | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    description: str = Field(default="", max_length=4000)
    priority: int = Field(default=0, ge=0, le=9)
    operation_id: str = Field(min_length=1, max_length=128)

    @field_validator("due_at", "remind_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Task timestamps must include UTC offsets")
        return value

    @model_validator(mode="after")
    def single_deadline(self) -> "TaskCreate":
        if self.due_date and self.due_at:
            raise ValueError("Use due_date or due_at, not both")
        return self


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: str
    expected_revision: int | str
    operation_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    planned_on: date | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    description: str | None = Field(default=None, max_length=4000)
    priority: int | None = Field(default=None, ge=0, le=9)
    status: Literal["open", "done", "cancelled"] | None = None

    @field_validator("task_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        identifier = value.removeprefix("ext_")
        if len(identifier) != 32 or any(
            c not in "0123456789abcdef" for c in identifier
        ):
            raise ValueError("Invalid task ID")
        return value

    @field_validator("due_at", "remind_at")
    @classmethod
    def aware_time(cls, value: datetime | None) -> datetime | None:
        return TaskCreate.aware_time(value)

    @model_validator(mode="after")
    def valid_patch(self) -> "TaskUpdate":
        for name in ("title", "status", "description", "priority"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if self.due_date and self.due_at:
            raise ValueError("Use due_date or due_at, not both")
        if isinstance(self.expected_revision, int):
            if self.expected_revision < 1:
                raise ValueError("Invalid revision")
        elif not self.expected_revision or len(self.expected_revision) > 512:
            raise ValueError("Invalid revision")
        return self


class Task(BaseModel):
    id: str
    owner: str
    title: str
    planned_on: date | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    remind_at: datetime | None = None
    description: str = ""
    priority: int = 0
    timezone: str = "Asia/Shanghai"
    source: str = "local"
    editable: bool = True
    status: Literal["open", "done", "cancelled"] = "open"
    revision: int | str = 1
    created_at: datetime
    updated_at: datetime
    operation_id: str
    creation_fingerprint: str

    def belongs_to_day(self, day: date) -> bool:
        deadline = self.due_date or (
            self.due_at.astimezone(ZoneInfo(self.timezone)).date()
            if self.due_at
            else None
        )
        return self.status == "open" and bool(
            self.planned_on == day or (deadline and deadline <= day)
        )
