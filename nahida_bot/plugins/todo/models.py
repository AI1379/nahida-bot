"""Task data and commands, independent of notification and UI implementations."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    planned_on: date | None = None
    due_date: date | None = None
    operation_id: str = Field(min_length=1, max_length=128)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: str
    expected_revision: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    planned_on: date | None = None
    due_date: date | None = None
    status: Literal["open", "done", "cancelled"] | None = None

    @field_validator("task_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Invalid task ID")
        return value


class Task(BaseModel):
    id: str
    owner: str
    title: str
    planned_on: date | None = None
    due_date: date | None = None
    status: Literal["open", "done", "cancelled"] = "open"
    revision: int = 1
    created_at: datetime
    updated_at: datetime
    operation_id: str
    creation_fingerprint: str

    def belongs_to_day(self, day: date) -> bool:
        return self.status == "open" and bool(
            self.planned_on == day or (self.due_date and self.due_date <= day)
        )
