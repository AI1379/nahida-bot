"""Configuration model for the Milky channel plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

GroupTriggerMode = Literal["none", "mention", "command", "always"]


class MilkyPluginConfig(BaseModel):
    """Runtime configuration for the Milky QQ channel.

    Milky uses HTTP ``/api/:api`` for outgoing API calls and WebSocket
    ``/event`` for inbound events. This model keeps those endpoint defaults
    explicit so the later client/event-stream implementation can stay small.
    """

    base_url: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:3000"),
        description="Base URL of the Lagrange.Milky HTTP service.",
    )
    access_token: str = Field(
        default="",
        description="Milky access token. Empty is only acceptable for local dev.",
    )
    api_prefix: str = Field(
        default="/api",
        description="HTTP API prefix before the Milky API name.",
    )
    event_path: str = Field(
        default="/event",
        description="WebSocket path used for inbound Milky events.",
    )
    ws_url: str = Field(
        default="",
        description="Optional full WebSocket URL override for the event stream.",
    )

    command_prefix: str = Field(
        default="/",
        min_length=1,
        description="Command prefix used for Milky messages.",
    )
    group_trigger_mode: GroupTriggerMode = Field(
        default="mention",
        description=(
            "How group messages trigger the bot: none, mention, command, or always. "
            "'mention' requires @bot; 'command' means command prefix or @bot."
        ),
    )
    group_context_capture: bool = Field(
        default=False,
        description=(
            "When true, non-triggering group messages are published as observed "
            "context instead of being dropped."
        ),
    )
    reply_to_inbound: bool | None = Field(
        default=None,
        description=(
            "Optional override for the router's default reply-to-inbound behavior. "
            "Null means use the global router setting."
        ),
    )
    allowed_friends: list[str] = Field(
        default_factory=list,
        description="Optional friend allow-list. Empty means all friends.",
    )
    allowed_groups: list[str] = Field(
        default_factory=list,
        description="Optional group allow-list. Empty means all groups.",
    )

    connect_timeout: float = Field(default=10.0, gt=0)
    heartbeat_timeout: float = Field(default=30.0, gt=0)
    reconnect_initial_delay: float = Field(default=1.0, gt=0)
    reconnect_max_delay: float = Field(default=30.0, gt=0)

    send_retry_attempts: int = Field(default=3, ge=1)
    send_retry_backoff: float = Field(default=1.0, gt=0)
    max_text_length: int = Field(default=4000, ge=1)

    media_download_dir: str = Field(default="./data/temp/media")
    enable_media_download_tool: bool = Field(default=True)
    resource_url_ttl_hint: int = Field(default=300, ge=0)
    cache_media_on_receive: bool = Field(
        default=True,
        description="Whether later inbound handling should eagerly cache media URLs.",
    )
    max_forward_depth: int = Field(
        default=3,
        ge=0,
        description="Maximum nested merged-forward depth to fetch/render.",
    )
    max_forward_messages: int = Field(
        default=80,
        ge=1,
        description="Maximum forwarded messages to process per resolved forward.",
    )
    forward_render_max_chars: int = Field(
        default=12000,
        ge=1,
        description="Maximum text budget for rendering resolved forwards.",
    )
    scene_cache_size: int = Field(
        default=4096,
        ge=1,
        description="Maximum peer->scene entries retained for outbound routing.",
    )
    outbound_mentions_enabled: bool = Field(
        default=True,
        description=(
            "Convert [CQ:at,qq=…] mention tokens in outbound text into real "
            "mention segments for group sends, after verifying each target is "
            "a current group member. Unverified tokens stay as literal text."
        ),
    )
    max_mentions_per_message: int = Field(
        default=3,
        ge=1,
        description="Maximum distinct mention targets converted per message.",
    )
    mention_member_cache_seconds: float = Field(
        default=180.0,
        gt=0,
        description=(
            "TTL for cached group-membership lookups used to validate "
            "outbound mention targets."
        ),
    )
    pending_file_ttl_seconds: float = Field(
        default=600.0,
        gt=0,
        description=(
            "How long a received file stays in the pending-file queue before "
            "expiring. Pending files are injected into the next message that "
            "triggers the agent for the same chat."
        ),
    )

    @field_validator(
        "allowed_friends",
        "allowed_groups",
        mode="before",
    )
    @classmethod
    def _coerce_id_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        raise TypeError("allowed id lists must be a string, integer, or list")

    @field_validator("api_prefix", "event_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("path must not be empty")
        return value if value.startswith("/") else f"/{value}"

    @field_validator("ws_url")
    @classmethod
    def _validate_ws_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        return value

    @model_validator(mode="after")
    def _validate_reconnect_delays(self) -> MilkyPluginConfig:
        if self.reconnect_max_delay < self.reconnect_initial_delay:
            raise ValueError(
                "reconnect_max_delay must be greater than or equal to "
                "reconnect_initial_delay"
            )
        return self

    @property
    def normalized_base_url(self) -> str:
        """Base URL without a trailing slash."""
        return str(self.base_url).rstrip("/")

    @property
    def api_base_url(self) -> str:
        """HTTP API base URL before the concrete Milky API name."""
        return f"{self.normalized_base_url}{self.api_prefix}"

    @property
    def event_ws_url(self) -> str:
        """Full WebSocket URL for Milky ``/event``."""
        if self.ws_url:
            return self.ws_url

        url = str(self.base_url)
        if url.startswith("https://"):
            scheme = "wss://"
            rest = url[len("https://") :]
        elif url.startswith("http://"):
            scheme = "ws://"
            rest = url[len("http://") :]
        else:
            return url.rstrip("/") + self.event_path

        return f"{scheme}{rest.rstrip('/')}{self.event_path}"


def parse_milky_config(raw: dict[str, Any] | None) -> MilkyPluginConfig:
    """Parse a plugin manifest config mapping into ``MilkyPluginConfig``."""
    return MilkyPluginConfig.model_validate(raw or {})
