"""Command result types and command metadata for nahida-bot plugins."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot_sdk.messaging import OutboundMessage


@dataclass(slots=True, frozen=True)
class CommandResult:
    """Structured result returned by a command handler."""

    message: OutboundMessage | None = None
    suppress_response: bool = False

    @classmethod
    def text(cls, text: str) -> "CommandResult":
        """Create a result that sends a plain text response."""
        return cls(message=OutboundMessage(text=text))

    @classmethod
    def none(cls) -> "CommandResult":
        """Create a result that intentionally sends no response."""
        return cls(suppress_response=True)


CommandHandlerResult = str | OutboundMessage | CommandResult | None


@dataclass(slots=True, frozen=True)
class CommandInfo:
    """Public metadata about a registered command."""

    name: str
    description: str
    aliases: tuple[str, ...]
    plugin_id: str


@dataclass(slots=True, frozen=True)
class CommandMatch:
    """Result of attempting to match a command from a message."""

    matched: bool
    name: str = ""
    args: str = ""
