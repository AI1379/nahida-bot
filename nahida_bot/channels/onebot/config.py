"""Configuration model for the OneBot channel plugin."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

GroupTriggerMode = Literal["none", "mention", "command", "always"]


class OneBotPluginConfig(BaseModel):
    """OneBot channel plugin configuration, protocol-version agnostic."""

    # --- Protocol version ---
    protocol_version: Literal["v11", "v12", "auto"] = "v11"

    # --- Forward WS mode ---
    ws_url: str = ""
    ws_access_token: str = ""

    # --- WebHook mode ---
    webhook_enabled: bool = False
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 6186
    webhook_path: str = "/onebot/event"
    webhook_secret: str = ""

    # --- HTTP API (v12 or WebHook mode outbound API calls) ---
    impl_base_url: str = ""
    impl_access_token: str = ""

    # --- Common ---
    command_prefix: str = Field(default="/", min_length=1)
    group_trigger_mode: GroupTriggerMode = Field(
        default="mention",
        description=(
            "How group messages trigger the bot: none, mention, command, or always. "
            "'mention' requires @bot; 'command' means command prefix or @bot."
        ),
    )
    group_context_capture: bool = False
    reply_to_inbound: bool | None = None

    allowed_friends: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)

    # --- Reconnect (forward WS) ---
    reconnect_initial_delay: float = Field(default=1.0, gt=0)
    reconnect_max_delay: float = Field(default=30.0, gt=0)

    # --- Messaging ---
    max_text_length: int = Field(default=4000, ge=1)
    split_long_text: bool = True

    # --- Media ---
    media_download_dir: str = "./data/temp/onebot"
    enable_media_download_tool: bool = True
    cache_media_on_receive: bool = True

    @field_validator("ws_url")
    @classmethod
    def _validate_ws_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        return value

    @model_validator(mode="after")
    def _validate_supported_modes(self) -> OneBotPluginConfig:
        if self.protocol_version == "v12":
            raise ValueError("OneBot v12 support is not yet implemented")
        if self.webhook_enabled:
            raise ValueError("OneBot WebHook mode is not yet implemented; use ws_url")
        if not self.ws_url:
            raise ValueError("OneBot forward WebSocket mode requires ws_url")
        return self

    @model_validator(mode="after")
    def _validate_reconnect_delays(self) -> OneBotPluginConfig:
        if self.reconnect_max_delay < self.reconnect_initial_delay:
            raise ValueError("reconnect_max_delay must be >= reconnect_initial_delay")
        return self

    @property
    def derived_http_base_url(self) -> str:
        """Derive HTTP API base from ws_url if not explicitly configured."""
        if self.impl_base_url:
            return self.impl_base_url.rstrip("/")
        if self.ws_url:
            return (
                self.ws_url.replace("ws://", "http://")
                .replace("wss://", "https://")
                .rstrip("/")
            )
        return ""


def parse_onebot_config(raw: dict[str, Any] | None) -> OneBotPluginConfig:
    """Parse a plugin manifest config mapping into ``OneBotPluginConfig``."""
    return OneBotPluginConfig.model_validate(raw or {})
