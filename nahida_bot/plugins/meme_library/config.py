"""Configuration for the curated meme library."""

from pydantic import BaseModel, Field, field_validator


class MemeLibraryConfig(BaseModel):
    """Library paths are configured by the operator, never by a send tool."""

    directory: str = Field(default="./data/memes", min_length=1)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    max_items: int = Field(default=2000, ge=1)
    refresh_seconds: float = Field(default=30, ge=0)
    send_cooldown_seconds: float = Field(default=10, ge=0)
    allowed_chats: list[str] = Field(default_factory=list)
    collect_enabled: bool = False
    collect_cooldown_seconds: float = Field(default=30, ge=0)

    @field_validator("allowed_chats")
    @classmethod
    def _typed_chats(cls, values: list[str]) -> list[str]:
        from nahida_bot.core.chat_address import ChatAddress

        for value in values:
            if not ChatAddress.parse(value).is_typed:
                raise ValueError(
                    "allowed_chats requires typed platform:type:id addresses"
                )
        return values
