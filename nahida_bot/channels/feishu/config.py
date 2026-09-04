"""Configuration model for the Feishu channel plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

GroupTriggerMode = Literal["none", "mention", "command", "always"]

_DOMESTIC_DOMAIN = "https://open.feishu.cn"
_INTERNATIONAL_DOMAIN = "https://open.larksuite.com"


class FeishuPluginConfig(BaseModel):
    """Runtime configuration for the Feishu channel.

    Feishu pushes events over the official SDK's WebSocket long connection
    (no public endpoint needed) and receives API calls through the OpenAPI
    HTTP endpoints under ``domain``.
    """

    app_id: str = Field(default="", description="Feishu custom app id (cli_…).")
    app_secret: str = Field(default="", description="Feishu custom app secret.")

    domain: str = Field(
        default=_DOMESTIC_DOMAIN,
        description=(
            "OpenAPI domain. https://open.feishu.cn for the domestic Feishu, "
            "https://open.larksuite.com for international Lark."
        ),
    )

    command_prefix: str = Field(default="/", min_length=1)
    group_trigger_mode: GroupTriggerMode = Field(
        default="mention",
        description=(
            "How group messages trigger the bot. NOTE: with only the "
            "im:message.group_at_msg:readonly scope the platform only delivers "
            "@bot messages, so 'always' additionally requires the sensitive "
            "im:message.group_msg scope."
        ),
    )
    group_context_capture: bool = Field(
        default=False,
        description=(
            "When true, non-triggering group messages are published as "
            "observed context instead of being dropped. Requires the "
            "sensitive im:message.group_msg scope (admin approval); without "
            "it the platform never delivers non-@bot group messages."
        ),
    )
    reply_to_inbound: bool | None = Field(
        default=None,
        description="Optional override for the router's reply-to-inbound default.",
    )
    allowed_chats: list[str] = Field(
        default_factory=list,
        description="Optional chat_id (oc_…) allow-list covering group and p2p chats.",
    )
    allowed_users: list[str] = Field(
        default_factory=list,
        description="Optional open_id (ou_…) allow-list for p2p senders.",
    )

    connect_timeout: float = Field(default=15.0, gt=0)
    send_retry_attempts: int = Field(default=3, ge=1)
    send_retry_backoff: float = Field(default=1.5, gt=0)
    max_text_length: int = Field(
        default=3500,
        ge=200,
        description="Outbound text split threshold; post/card hard limit is 30 KB.",
    )

    markdown_enabled: bool = Field(
        default=True,
        description=(
            "Render outbound Markdown as Feishu rich-text (post) messages "
            "with bold/italic/strikethrough/code/links. Falls back to plain "
            "text when a post send fails."
        ),
    )

    outbound_mentions_enabled: bool = Field(
        default=True,
        description=(
            "Convert [CQ:at,qq=ou_…] mention tokens in outbound text into "
            "real <at> tags for group sends, after verifying the target is a "
            "current chat member. Unverified tokens stay as literal text."
        ),
    )
    max_mentions_per_message: int = Field(default=3, ge=1)
    member_cache_seconds: float = Field(
        default=1800.0,
        gt=0,
        description=(
            "TTL for cached chat member lookups; backs both outbound mention "
            "validation and inbound sender display names."
        ),
    )

    media_download_dir: str = Field(default="./data/temp/media")
    enable_media_download_tool: bool = Field(default=True)
    cache_media_on_receive: bool = Field(
        default=True,
        description="Eagerly download inbound media into the shared media cache.",
    )

    @field_validator("app_id", "app_secret")
    @classmethod
    def _strip_secret(cls, value: str) -> str:
        return value.strip()

    @field_validator("domain")
    @classmethod
    def _normalize_domain(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return _DOMESTIC_DOMAIN
        if not value.startswith(("https://", "http://")):
            value = f"https://{value}"
        return value

    @field_validator("allowed_chats", "allowed_users", mode="before")
    @classmethod
    def _coerce_id_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            return [str(value)]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise TypeError("allowed id lists must be a string, integer, or list")

    @property
    def api_base(self) -> str:
        """OpenAPI base URL, e.g. ``https://open.feishu.cn/open-apis``."""
        return f"{self.domain}/open-apis"

    @property
    def is_international(self) -> bool:
        """Whether the configured domain targets international Lark."""
        return self.domain.startswith(_INTERNATIONAL_DOMAIN)


def parse_feishu_config(raw: dict[str, Any] | None) -> FeishuPluginConfig:
    """Parse a plugin manifest config mapping into ``FeishuPluginConfig``."""
    return FeishuPluginConfig.model_validate(raw or {})
