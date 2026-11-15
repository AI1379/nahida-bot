"""Trusted connection configuration; tools never accept URLs or credentials."""

from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    username: str
    password_env: str = Field(default="", pattern=r"^$|^[A-Za-z_][A-Za-z0-9_]*$")
    password_secret: str = ""
    allowed_origins: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=8, ge=1, le=12)
    allow_local_http: bool = False

    @model_validator(mode="after")
    def validate_connection(self) -> "Connection":
        if bool(self.password_env) == bool(self.password_secret):
            raise ValueError("Set exactly one password_env or password_secret")
        for value in [self.url, *self.allowed_origins]:
            parsed = urlsplit(value)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CalDAV URLs must have a host and no embedded credentials, query or fragment"
                )
            local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and local and self.allow_local_http
            ):
                raise ValueError(
                    "CalDAV requires HTTPS; explicit loopback HTTP is allowed for tests"
                )
        return self


class TaskBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: str
    collection: str = ""


class CalendarBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: str
    read_collections: list[str] = Field(default_factory=list, max_length=16)
    write_collection: str = ""


class AccountBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "Asia/Shanghai"
    tasks: TaskBinding | None = None
    calendar: CalendarBinding | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Unknown timezone") from exc
        return value


class CalDAVConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connections: dict[str, Connection] = Field(default_factory=dict)
    bindings: dict[str, AccountBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_connections(self) -> "CalDAVConfig":
        for actor, binding in self.bindings.items():
            if ":user:" not in actor:
                raise ValueError("Bindings require a canonical account key")
            for source in (binding.tasks, binding.calendar):
                if source and source.connection not in self.connections:
                    raise ValueError("Binding references an unknown CalDAV connection")
        return self

    def account(self, actor: str, role: Literal["tasks", "calendar"]) -> AccountBinding:
        binding = self.bindings.get(actor)
        if not actor or binding is None or getattr(binding, role) is None:
            raise ValueError(f"No {role} CalDAV binding for this account")
        return binding
