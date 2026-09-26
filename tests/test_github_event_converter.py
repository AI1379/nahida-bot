"""Tests for the GitHub webhook payload converter."""

from __future__ import annotations

from typing import Any

import pytest

from nahida_bot.channels.github.config import GitHubChannelConfig
from nahida_bot.channels.github.event_converter import GitHubEventConverter

pytestmark = pytest.mark.asyncio

REPO = "AI1379/nahida-bot"


def _payload(
    *,
    action: str = "opened",
    login: str = "visitor",
    sender_type: str = "User",
) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"full_name": REPO},
        "sender": {"login": login, "type": sender_type},
    }


def _issue_comment(body: str = "please look at this") -> dict[str, Any]:
    payload = _payload(action="created")
    payload["issue"] = {
        "number": 31,
        "id": 2222,
        "title": "feat: 接入 GitHub Bot",
        "body": "原始描述",
        "created_at": "2026-06-28T16:07:11Z",
    }
    payload["comment"] = {
        "id": 9999,
        "body": body,
        "created_at": "2026-09-25T08:00:00Z",
    }
    return payload


def _issues(action: str = "opened") -> dict[str, Any]:
    payload = _payload(action=action)
    payload["issue"] = {
        "number": 60,
        "id": 3333,
        "title": "workspace 核心文件保护",
        "body": "允许修改但禁止删除",
        "state": "open",
        "created_at": "2026-09-19T12:40:33Z",
    }
    return payload


def _pull_request(action: str = "opened", *, merged: bool = False) -> dict[str, Any]:
    payload = _payload(action=action)
    payload["number"] = 12
    payload["pull_request"] = {
        "number": 12,
        "id": 4444,
        "title": "feat: feishu channel",
        "body": "",
        "merged": merged,
        "created_at": "2026-08-01T00:00:00Z",
    }
    return payload


def _converter(checker: Any = None, **config_kwargs: Any) -> GitHubEventConverter:
    return GitHubEventConverter(
        GitHubChannelConfig(**config_kwargs),
        bot_login="nahida-bot",
        org_member_checker=checker,
    )


async def test_issue_comment_converted_as_message() -> None:
    converted = await _converter().to_inbound("issue_comment", _issue_comment())

    assert converted is not None
    assert converted.kind == "message"
    inbound = converted.inbound
    assert inbound.platform == "github"
    assert inbound.chat_id == f"{REPO}#31"
    assert inbound.is_group is True
    assert inbound.user_id == "visitor"
    assert inbound.text == "please look at this"
    assert inbound.message_id == "9999"
    assert inbound.timestamp > 0
    assert inbound.mentions_bot is False
    assert inbound.sender_context is not None
    assert inbound.sender_context.platform_user_id == "visitor"
    assert inbound.chat_context is not None
    assert inbound.chat_context.platform_chat_id == f"{REPO}#31"
    assert "feat: 接入 GitHub Bot" in inbound.chat_context.display_name
    assert inbound.message_context is not None
    assert inbound.sender_account_key == "github:user:visitor"


async def test_mention_detection_case_insensitive() -> None:
    payload = _issue_comment(body="thanks @Nahida-Bot for the fix")
    converted = await _converter().to_inbound("issue_comment", payload)

    assert converted is not None
    assert converted.inbound.mentions_bot is True


async def test_mention_requires_boundary() -> None:
    payload = _issue_comment(body="ping @nahida-bot2 and @xnahida-bot")
    converted = await _converter().to_inbound("issue_comment", payload)

    assert converted is not None
    assert converted.inbound.mentions_bot is False


async def test_mention_needs_bot_login() -> None:
    converter = GitHubEventConverter(GitHubChannelConfig())
    converted = await converter.to_inbound(
        "issue_comment", _issue_comment(body="hey @nahida-bot")
    )

    assert converted is not None
    assert converted.inbound.mentions_bot is False


async def test_issue_opened_uses_body() -> None:
    converted = await _converter().to_inbound("issues", _issues())

    assert converted is not None
    assert converted.kind == "message"
    assert converted.inbound.text == "允许修改但禁止删除"
    assert converted.inbound.message_id == "issues:opened:3333"


async def test_issue_opened_empty_body_gets_placeholder() -> None:
    payload = _issues()
    payload["issue"]["body"] = ""
    converted = await _converter().to_inbound("issues", payload)

    assert converted is not None
    assert converted.inbound.text == "(_no description_)"


async def test_pr_opened_marks_kind_in_display_name() -> None:
    converted = await _converter().to_inbound("pull_request", _pull_request())

    assert converted is not None
    assert converted.kind == "message"
    assert converted.inbound.chat_context is not None
    assert converted.inbound.chat_context.display_name.startswith(f"{REPO}#12 · PR ")


async def test_pr_review_converted() -> None:
    payload = _payload(action="submitted")
    payload["pull_request"] = {"number": 12, "id": 4444, "title": "feat: x"}
    payload["review"] = {
        "id": 5555,
        "body": "",
        "state": "approved",
        "submitted_at": "2026-09-25T09:00:00Z",
    }
    converted = await _converter().to_inbound("pull_request_review", payload)

    assert converted is not None
    assert converted.kind == "message"
    assert "approved" in converted.inbound.text


async def test_pr_review_comment_converted() -> None:
    payload = _payload(action="created")
    payload["pull_request"] = {"number": 12, "id": 4444, "title": "feat: x"}
    payload["comment"] = {"id": 6666, "body": "inline note", "created_at": ""}
    converted = await _converter().to_inbound("pull_request_review_comment", payload)

    assert converted is not None
    assert converted.inbound.text == "inline note"
    assert converted.inbound.message_id == "6666"


async def test_issue_closed_is_context_kind() -> None:
    converted = await _converter().to_inbound("issues", _issues(action="closed"))

    assert converted is not None
    assert converted.kind == "context"
    assert "closed" in converted.inbound.text


async def test_pr_closed_merged_noted() -> None:
    converted = await _converter().to_inbound(
        "pull_request", _pull_request("closed", merged=True)
    )

    assert converted is not None
    assert converted.kind == "context"
    assert "(merged)" in converted.inbound.text


async def test_lifecycle_disabled_drops_context_events() -> None:
    converter = GitHubEventConverter(GitHubChannelConfig(lifecycle_as_context=False))

    assert await converter.to_inbound("issues", _issues(action="closed")) is None


async def test_unsupported_event_dropped() -> None:
    assert await _converter().to_inbound("push", _payload()) is None
    assert await _converter().to_inbound("star", _payload(action="created")) is None


async def test_unsupported_action_dropped() -> None:
    payload = _issue_comment() | {"action": "deleted"}
    assert await _converter().to_inbound("issue_comment", payload) is None


async def test_bot_sender_dropped() -> None:
    payload = _issue_comment()
    payload["sender"]["type"] = "Bot"

    assert await _converter().to_inbound("issue_comment", payload) is None


async def test_self_sender_dropped() -> None:
    payload = _issue_comment()
    payload["sender"]["login"] = "Nahida-Bot"

    assert await _converter().to_inbound("issue_comment", payload) is None


async def test_repo_allow_list_enforced() -> None:
    converter = GitHubEventConverter(
        GitHubChannelConfig(allowed_repos=["other/repo"]), bot_login="nahida-bot"
    )

    assert await converter.to_inbound("issue_comment", _issue_comment()) is None


async def test_repo_org_wildcard_allows_all_repos_in_org() -> None:
    converter = GitHubEventConverter(
        GitHubChannelConfig(allowed_repos=["AI1379/*"]), bot_login="nahida-bot"
    )
    other_org = _issue_comment()
    other_org["repository"]["full_name"] = "AI1379X/other"

    assert await converter.to_inbound("issue_comment", _issue_comment()) is not None
    assert await converter.to_inbound("issue_comment", other_org) is None


async def test_sender_allow_list_enforced() -> None:
    converter = GitHubEventConverter(
        GitHubChannelConfig(allowed_senders=["friend"]), bot_login="nahida-bot"
    )

    assert await converter.to_inbound("issue_comment", _issue_comment()) is None
    payload = _issue_comment()
    payload["sender"]["login"] = "Friend"
    assert await converter.to_inbound("issue_comment", payload) is not None


async def test_org_member_passes_when_orgs_configured() -> None:
    calls: list[str] = []

    async def checker(login: str) -> bool:
        calls.append(login)
        return True

    converter = _converter(checker, allowed_orgs=["AI1379"])
    converted = await converter.to_inbound("issue_comment", _issue_comment())

    assert converted is not None
    assert calls == ["visitor"]


async def test_org_non_member_dropped_when_orgs_configured() -> None:
    async def checker(login: str) -> bool:
        return False

    converter = _converter(checker, allowed_orgs=["AI1379"])

    assert await converter.to_inbound("issue_comment", _issue_comment()) is None


async def test_orgs_configured_without_checker_denies_non_static_sender() -> None:
    converter = _converter(checker=None, allowed_orgs=["AI1379"])

    assert await converter.to_inbound("issue_comment", _issue_comment()) is None


async def test_static_sender_skips_org_probe() -> None:
    calls: list[str] = []

    async def checker(login: str) -> bool:
        calls.append(login)
        return False

    converter = _converter(checker, allowed_senders=["visitor"], allowed_orgs=["X"])
    converted = await converter.to_inbound("issue_comment", _issue_comment())

    assert converted is not None
    assert calls == []


async def test_empty_lists_allow_everyone_without_probe() -> None:
    calls: list[str] = []

    async def checker(login: str) -> bool:
        calls.append(login)
        return False

    converter = _converter(checker)
    converted = await converter.to_inbound("issue_comment", _issue_comment())

    assert converted is not None
    assert calls == []


async def test_missing_repository_dropped() -> None:
    payload = _issue_comment()
    del payload["repository"]

    assert await _converter().to_inbound("issue_comment", payload) is None


async def test_with_bot_login_returns_new_instance() -> None:
    async def checker(login: str) -> bool:
        return True

    base = GitHubEventConverter(GitHubChannelConfig(), org_member_checker=checker)
    updated = base.with_bot_login("someone")

    assert base.bot_login == ""
    assert updated.bot_login == "someone"
    assert await base.with_bot_login("x").to_inbound("push", {}) is None
