"""Command registry and message-to-command matcher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from nahida_bot.plugins.base import OutboundMessage


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
    """Public command metadata exposed through BotAPI."""

    name: str
    description: str
    aliases: tuple[str, ...]
    plugin_id: str


@dataclass(slots=True, frozen=True)
class CommandEntry:
    """A registered command with its handler and metadata."""

    name: str
    handler: Callable[..., Awaitable[CommandHandlerResult]]
    description: str
    aliases: tuple[str, ...]
    plugin_id: str

    def to_info(self) -> CommandInfo:
        """Return public metadata for this command."""
        return CommandInfo(
            name=self.name,
            description=self.description,
            aliases=self.aliases,
            plugin_id=self.plugin_id,
        )


@dataclass(slots=True, frozen=True)
class CommandMatch:
    """Result of attempting to match a command in a message."""

    matched: bool
    name: str = ""
    args: str = ""


class CommandRegistry:
    """Registry mapping command names and aliases to their handlers."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandEntry] = {}  # name/alias → entry

    def register(self, entry: CommandEntry) -> None:
        """Register a command and its aliases.

        Raises ``KeyError`` if the name or any alias is already taken.
        """
        names = [entry.name, *entry.aliases]
        for name in names:
            if name in self._commands:
                existing = self._commands[name]
                raise KeyError(
                    f"Command '{name}' is already registered by plugin "
                    f"'{existing.plugin_id}'"
                )
        for name in names:
            self._commands[name] = entry

    def unregister(self, name: str) -> None:
        """Remove a command by its primary name (also removes aliases)."""
        entry = self._commands.pop(name, None)
        if entry is not None:
            for alias in entry.aliases:
                self._commands.pop(alias, None)

    def get(self, name: str) -> CommandEntry | None:
        """Look up a command by name or alias."""
        return self._commands.get(name)

    def all_commands(self) -> list[CommandEntry]:
        """Return unique command entries (deduplicated by primary name)."""
        seen: set[str] = set()
        result: list[CommandEntry] = []
        for entry in self._commands.values():
            if entry.name not in seen:
                seen.add(entry.name)
                result.append(entry)
        return result

    def unregister_by_plugin(self, plugin_id: str) -> int:
        """Remove all commands owned by a plugin. Returns count of primary names removed."""
        to_remove: set[str] = set()
        for entry in self._commands.values():
            if entry.plugin_id == plugin_id:
                to_remove.add(entry.name)
        for name in to_remove:
            self.unregister(name)
        return len(to_remove)


# Pattern: optional leading @mention, then /command, then rest as args.
# Matches: "/help", "/search foo bar", "@bot /ping", "@bot/help arg"
_MENTION_PATTERN = re.compile(
    r"^\s*@?\S*?\s*"  # optional @mention prefix
)
_COMMAND_PATTERN = re.compile(
    r"^\s*(@\S+\s+)?"  # optional @mention + space
    r"([/!])(\w+)"  # prefix char (/ or !) + command name
    r"(?:\s+(.*))?$",  # optional args after space
    re.DOTALL,
)


class CommandMatcher:
    """Parse a message string to extract command name and arguments.

    Supports configurable prefix characters (default ``/``). Automatically
    strips leading @mention tokens so that ``@bot /help`` is treated the
    same as ``/help``.
    """

    def __init__(self, prefix: str = "/") -> None:
        self._prefix = prefix

    def match(self, text: str, *, prefix: str = "") -> CommandMatch:
        """Try to extract a command from message text.

        Args:
            text: The message body to parse.
            prefix: Override prefix for this specific message (e.g. from
                ``InboundMessage.command_prefix``). Falls back to the
                matcher's default if empty.

        Returns:
            ``CommandMatch`` with ``matched=True`` if a command was found.
        """
        effective_prefix = prefix or self._prefix
        if not text:
            return CommandMatch(matched=False)

        # Strip leading @mention (e.g. "@botname " or "@botname/")
        stripped = text.strip()
        m = _COMMAND_PATTERN.match(stripped)
        if m is None:
            return CommandMatch(matched=False)

        actual_prefix = m.group(2)
        command_name = m.group(3)
        args = (m.group(4) or "").strip()

        if actual_prefix != effective_prefix:
            return CommandMatch(matched=False)

        return CommandMatch(matched=True, name=command_name, args=args)
