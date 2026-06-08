"""Tests for group-chat observe/respond policy."""

from __future__ import annotations

import pytest

from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.plugins.base import InboundMessage


def _message(
    *,
    text: str = "hello",
    is_group: bool = True,
    mentions_bot: bool = False,
    command_prefix: str = "/",
) -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="test",
        chat_id="c1",
        user_id="u1",
        text=text,
        raw_event={},
        is_group=is_group,
        mentions_bot=mentions_bot,
        command_prefix=command_prefix,
    )


@pytest.mark.parametrize(
    ("mode", "text", "mentions_bot", "respond", "reason"),
    [
        ("none", "hello", False, False, "ignored_untriggered"),
        ("none", "/help", True, False, "ignored_untriggered"),
        ("mention", "hello", False, False, "ignored_untriggered"),
        ("mention", "/help", False, False, "ignored_untriggered"),
        ("mention", "help", True, True, "mention"),
        ("mention", "/help", True, True, "mention"),
        ("command", "hello", False, False, "ignored_untriggered"),
        ("command", "/help", False, True, "command"),
        ("command", "help", True, True, "mention"),
        ("always", "hello", False, True, "always"),
    ],
)
def test_group_trigger_modes(
    mode: str,
    text: str,
    mentions_bot: bool,
    respond: bool,
    reason: str,
) -> None:
    decision = GroupInteractionPolicy(mode=mode).decide(
        _message(text=text, mentions_bot=mentions_bot)
    )

    assert decision.respond is respond
    assert decision.observe is respond
    assert decision.reason == reason


def test_untriggered_group_message_can_be_observed() -> None:
    decision = GroupInteractionPolicy(
        mode="mention",
        observe_untriggered=True,
    ).decide(_message(text="/help", mentions_bot=False))

    assert decision.observe is True
    assert decision.respond is False
    assert decision.reason == "observed_untriggered"


def test_private_message_always_responds() -> None:
    decision = GroupInteractionPolicy(mode="none").decide(_message(is_group=False))

    assert decision.observe is True
    assert decision.respond is True
    assert decision.reason == "private"
