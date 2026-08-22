"""Command result types and command metadata for nahida-bot plugins."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Union

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
    def outbound(cls, message: OutboundMessage) -> "CommandResult":
        """Create a result that sends a full outbound message."""
        return cls(message=message)

    @classmethod
    def none(cls) -> "CommandResult":
        """Create a result that intentionally sends no response."""
        return cls(suppress_response=True)


CommandHandlerResult = str | OutboundMessage | CommandResult | None


# ── Argument completion (channel-agnostic) ────────────────────
#
# Completers MUST be fast local lookups (registry/config queries).
# Discord answers autocomplete interactions with no defer and a 3s
# deadline; slow completers degrade to empty suggestions.


@dataclass(slots=True, frozen=True)
class CompletionQuery:
    """One autocomplete request for a command argument."""

    command: str
    argument: str
    partial: str = ""  # text the user already typed into the field
    filled: Mapping[str, str] = field(default_factory=dict)  # other args already set
    session_id: str = ""
    user_id: str = ""
    is_admin: bool = False


@dataclass(slots=True, frozen=True)
class CompletionChoice:
    """One candidate value for an autocomplete response."""

    value: str  # text filled into the field when chosen
    display: str = ""  # UI label; defaults to value
    description: str = ""  # one-line hint shown next to the label


CompletionChoiceLike = Union[CompletionChoice, str]
CompleterFn = Callable[[CompletionQuery], Awaitable[Sequence[CompletionChoiceLike]]]


@dataclass(slots=True, frozen=True)
class CommandArgument:
    """Declarative metadata for one command argument.

    Powers native command UIs (Discord slash options) and autocomplete.
    Text invocation (``/model name``) is unaffected: handlers keep
    parsing the freeform args string.
    """

    name: str
    description: str = ""
    type: str = "string"  # string / int / float / bool / user / channel
    required: bool = False
    # Static or lazy enum; filtered by ``partial`` prefix automatically.
    choices: Union[tuple[str, ...], Callable[[], Sequence[str]], None] = None
    # Dynamic completion; takes precedence over ``choices``. Receives the
    # raw query (responsible for its own filtering).
    completer: CompleterFn | None = None


@dataclass(slots=True, frozen=True)
class CommandInfo:
    """Public metadata about a registered command."""

    name: str
    description: str
    aliases: tuple[str, ...]
    plugin_id: str
    arguments: tuple[CommandArgument, ...] = ()


@dataclass(slots=True, frozen=True)
class CommandMatch:
    """Result of attempting to match a command from a message."""

    matched: bool
    name: str = ""
    args: str = ""
