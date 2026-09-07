"""Action authorization settings, independent of Person resolution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nahida_bot_sdk.chat_address import ChatAddress


AuthorizationMode = Literal["standard", "relaxed", "unsafe"]


class RiskReviewConfig(BaseModel):
    """Tool-call review is a heuristic, not an execution sandbox."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "cheap"
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_input_chars: int = Field(default=60000, ge=1000, le=200000)


class AuthorizationConfig(BaseModel):
    """Select policy globally or by authenticated account / typed chat.

    Explicit account policy wins over chat policy, then the default. An omitted
    admin list inherits identity.admins for migration; an empty list does not.
    Both relaxed and unsafe currently execute with the bot's OS permissions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: AuthorizationMode = "standard"
    admins: list[str] | None = None
    accounts: dict[str, AuthorizationMode] = Field(default_factory=dict)
    chats: dict[str, AuthorizationMode] = Field(default_factory=dict)
    review: RiskReviewConfig = Field(default_factory=RiskReviewConfig)

    @field_validator("admins")
    @classmethod
    def validate_admins(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            for account in value:
                cls._validate_account(account)
        return value

    @field_validator("accounts")
    @classmethod
    def validate_accounts(
        cls, value: dict[str, AuthorizationMode]
    ) -> dict[str, AuthorizationMode]:
        for account in value:
            cls._validate_account(account)
        return value

    @staticmethod
    def _validate_account(value: str) -> None:
        # Avoid importing identity.models through the config import graph.
        channel, separator, account = value.partition(":user:")
        if not separator or not channel or not account or value != value.strip():
            raise ValueError(
                "Expected a canonical account key: channel:user:account_id"
            )
        if ":" in account or any(char.isspace() for char in value):
            raise ValueError("Invalid account key")

    @field_validator("chats")
    @classmethod
    def validate_chats(
        cls, value: dict[str, AuthorizationMode]
    ) -> dict[str, AuthorizationMode]:
        for key in value:
            address = ChatAddress.parse(key)
            if (
                not address.is_typed
                or address.chat_key != key
                or ":" in address.target_id
            ):
                raise ValueError("Expected a canonical typed chat address")
        return value
