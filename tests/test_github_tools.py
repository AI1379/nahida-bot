"""Tests for the github_* issue tools."""

from __future__ import annotations

import json
from typing import Any

import pytest

from nahida_bot.channels.github.client import GitHubClientError
from nahida_bot.channels.github.config import GitHubChannelConfig
from nahida_bot.channels.github.tools import register_issue_tools

from .helpers import RecordingMockBotAPI

pytestmark = pytest.mark.asyncio


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        labels: tuple[str, ...] = (),
        limit: int = 20,
        include_pulls: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "list_issues",
                {"owner": owner, "repo": repo, "state": state, "labels": labels},
            )
        )
        return [
            {
                "number": 31,
                "title": "feat: GitHub Bot",
                "state": "open",
                "html_url": "u1",
                "comments": 2,
            },
            {
                "number": 30,
                "title": "old",
                "state": "open",
                "html_url": "u2",
                "comments": 0,
            },
        ]

    async def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        self.calls.append(
            ("get_issue", {"owner": owner, "repo": repo, "number": number})
        )
        return {
            "number": number,
            "title": "feat: GitHub Bot",
            "state": "open",
            "html_url": "u1",
            "comments": 2,
            "body": "original body",
            "user": {"login": "visitor"},
        }

    async def list_comments(
        self, owner: str, repo: str, number: int, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("list_comments", {"owner": owner, "repo": repo, "number": number})
        )
        return [
            {
                "user": {"login": "alice"},
                "created_at": "2026-09-25T00:00:00Z",
                "body": "first",
            },
            {
                "user": {"login": "bob"},
                "created_at": "2026-09-25T01:00:00Z",
                "body": "second",
            },
        ]

    async def create_issue(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        body: str = "",
        labels: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "create_issue",
                {
                    "owner": owner,
                    "repo": repo,
                    "title": title,
                    "body": body,
                    "labels": labels,
                },
            )
        )
        return {
            "number": 61,
            "title": title,
            "state": "open",
            "html_url": "u3",
            "comments": 0,
        }

    async def add_comment(
        self, owner: str, repo: str, number: int, *, body: str
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "add_comment",
                {"owner": owner, "repo": repo, "number": number, "body": body},
            )
        )
        return {"id": 777, "html_url": "u4"}

    async def update_issue(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        state: str = "",
        title: str = "",
        body: str = "",
        labels: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "update_issue",
                {"owner": owner, "repo": repo, "number": number, "state": state},
            )
        )
        return {
            "number": number,
            "title": "t",
            "state": state,
            "html_url": "u1",
            "comments": 0,
        }


class _FailingClient(_FakeClient):
    async def get_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        raise GitHubClientError("not found")


def _registered(
    **config_kwargs: Any,
) -> tuple[RecordingMockBotAPI, dict[str, dict[str, Any]], _FakeClient]:
    api = RecordingMockBotAPI()
    client = _FakeClient()
    config = GitHubChannelConfig(**config_kwargs)
    register_issue_tools(api, config, lambda: client)  # type: ignore[arg-type]
    return api, api.registered_tools, client


async def _run(
    api_tools: dict[str, dict[str, Any]], name: str, /, **kwargs: Any
) -> dict[str, Any]:
    return json.loads(await api_tools[name]["handler"](**kwargs))


async def test_five_tools_registered_with_admin_flag_on_update() -> None:
    api, tools, _ = _registered()

    assert set(tools) == {
        "github_list_issues",
        "github_get_issue",
        "github_create_issue",
        "github_add_comment",
        "github_update_issue",
    }
    assert tools["github_update_issue"]["requires_admin"] is True
    assert all(
        tools[name]["requires_admin"] is False
        for name in tools
        if name != "github_update_issue"
    )


async def test_list_issues_returns_summaries() -> None:
    api, tools, _ = _registered()

    result = await _run(tools, "github_list_issues", repo="AI1379/nahida-bot")

    assert result["count"] == 2
    assert result["issues"][0]["number"] == 31


async def test_get_issue_with_comments() -> None:
    api, tools, _ = _registered()

    result = await _run(
        tools,
        "github_get_issue",
        repo="AI1379/nahida-bot",
        number=31,
        include_comments=True,
        comment_limit=1,
    )

    assert result["number"] == 31
    assert result["user"] == "visitor"
    assert len(result["comments"]) == 1
    assert result["comments"][0]["user"] == "bob"


async def test_create_issue_passes_fields() -> None:
    api, tools, client = _registered()

    result = await _run(
        tools,
        "github_create_issue",
        repo="AI1379/nahida-bot",
        title="New bug",
        body="details",
        labels=["bug"],
    )

    assert result["number"] == 61
    create_call = client.calls[0]
    assert create_call[0] == "create_issue"
    assert create_call[1]["title"] == "New bug"
    assert create_call[1]["labels"] == ["bug"]


async def test_add_comment_returns_url() -> None:
    api, tools, _ = _registered()

    result = await _run(
        tools,
        "github_add_comment",
        repo="AI1379/nahida-bot",
        number=31,
        body="已处理",
    )

    assert result["id"] == 777
    assert result["html_url"] == "u4"


async def test_update_issue_requires_state_value() -> None:
    api, tools, _ = _registered()

    result = await _run(
        tools,
        "github_update_issue",
        repo="AI1379/nahida-bot",
        number=31,
        state="reopened",
    )

    assert "error" in result


async def test_update_issue_closes() -> None:
    api, tools, client = _registered()

    result = await _run(
        tools,
        "github_update_issue",
        repo="AI1379/nahida-bot",
        number=31,
        state="closed",
    )

    assert result["state"] == "closed"
    assert client.calls[0][1]["state"] == "closed"


async def test_repo_must_be_owner_name_form() -> None:
    api, tools, _ = _registered()

    result = await _run(tools, "github_list_issues", repo="just-a-name")

    assert "error" in result


async def test_allowed_repos_enforced() -> None:
    api, tools, client = _registered(allowed_repos=["AI1379/nahida-bot"])

    ok = await _run(tools, "github_list_issues", repo="ai1379/nahida-bot")
    denied = await _run(tools, "github_list_issues", repo="other/repo")

    assert ok["count"] == 2
    assert "error" in denied
    assert len(client.calls) == 1


async def test_client_error_returned_as_error_json() -> None:
    api = RecordingMockBotAPI()
    failing = _FailingClient()
    register_issue_tools(api, GitHubChannelConfig(), lambda: failing)  # type: ignore[arg-type]

    result = json.loads(
        await api.registered_tools["github_get_issue"]["handler"](repo="a/b", number=1)
    )

    assert "error" in result


async def test_create_issue_empty_title_rejected() -> None:
    api, tools, _ = _registered()

    result = await _run(tools, "github_create_issue", repo="a/b", title="   ")

    assert "error" in result
