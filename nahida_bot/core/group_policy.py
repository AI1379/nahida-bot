"""Shared group-chat observe/respond policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nahida_bot.plugins.base import InboundMessage

GroupTriggerMode = Literal["mention", "command", "always"]


@dataclass(slots=True, frozen=True)
class GroupInteractionDecision:
    """Decision for one inbound message."""

    observe: bool
    respond: bool
    reason: str


@dataclass(slots=True, frozen=True)
class GroupInteractionPolicy:
    """Decide whether a group message should be observed and/or answered."""

    mode: GroupTriggerMode = "always"
    observe_untriggered: bool = False

    def decide(self, message: InboundMessage) -> GroupInteractionDecision:
        """Return the interaction decision for a normalized inbound message."""
        if not message.is_group:
            return GroupInteractionDecision(
                observe=True,
                respond=True,
                reason="private",
            )

        command = _is_command(message)
        mention = message.mentions_bot

        if self.mode == "always":
            return GroupInteractionDecision(
                observe=True,
                respond=True,
                reason="always",
            )

        if command:
            return GroupInteractionDecision(
                observe=True,
                respond=True,
                reason="command",
            )

        if self.mode == "mention" and mention:
            return GroupInteractionDecision(
                observe=True,
                respond=True,
                reason="mention",
            )

        if self.observe_untriggered:
            return GroupInteractionDecision(
                observe=True,
                respond=False,
                reason="observed_untriggered",
            )

        return GroupInteractionDecision(
            observe=False,
            respond=False,
            reason="ignored_untriggered",
        )


def _is_command(message: InboundMessage) -> bool:
    prefix = message.command_prefix or "/"
    return bool(prefix and message.text.lstrip().startswith(prefix))
