"""Tests for the external GitHub notifier plugin."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "github-notifier"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from nahida_bot_sdk import (  # noqa: E402
    ChatContext,
    InboundMessage,
    OutboundMessage,
    PluginManifest,
    WebhookRequest,
)
from nahida_bot_sdk.testing import RecordingMockBotAPI  # noqa: E402
from nahida_plugin_github_notifier.plugin import (  # noqa: E402
    _GitHubItem,
    GitHubNotifierPlugin,
)


class _API(RecordingMockBotAPI):
    def __init__(self) -> None:
        super().__init__()
        self.sent_messages: list[tuple[str, OutboundMessage, str]] = []

    async def send_message(
        self, target: str, message: OutboundMessage, *, channel: str = ""
    ) -> str:
        self.sent_messages.append((target, message, channel))
        return f"msg-{len(self.sent_messages)}"


def _manifest(config: dict[str, Any]) -> PluginManifest:
    return PluginManifest(
        id="github-notifier",
        name="GitHub Notifier",
        version="0.1.0",
        entrypoint="nahida_plugin_github_notifier.plugin:GitHubNotifierPlugin",
        config=config,
    )


def _payload(
    *,
    event: str = "issues",
    action: str = "opened",
    repo: str = "octocat/hello",
    number: int = 123,
    title: str = "Bug report",
) -> dict[str, Any]:
    item = {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "state": "open" if action == "opened" else "closed",
        "user": {"login": "alice"},
    }
    return {
        "action": action,
        "repository": {"full_name": repo},
        "sender": {"login": "alice"},
        "issue" if event == "issues" else "pull_request": item,
    }


def _request(
    payload: dict[str, Any],
    *,
    event: str = "issues",
    delivery: str = "delivery-1",
    secret: str = "",
    signature: str | None = None,
) -> WebhookRequest:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-github-event": event,
        "x-github-delivery": delivery,
    }
    if signature is None and secret:
        signature = (
            "sha256="
            + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        )
    if signature is not None:
        headers["x-hub-signature-256"] = signature
    return WebhookRequest(
        method="POST",
        path="github",
        headers=headers,
        query={},
        body=body,
    )


async def _wait_for_sent(api: _API, count: int) -> None:
    for _ in range(20):
        if len(api.sent_messages) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(
        f"expected {count} sent messages, got {len(api.sent_messages)}"
    )


@pytest.mark.asyncio
async def test_webhook_sends_to_multiple_unique_targets_and_dedupes_delivery() -> None:
    api = _API()
    plugin = GitHubNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "repo": "octocat/hello",
                "target_chat_addresses": [
                    "milky:group:100",
                    "milky:group:100",
                    "telegram:private:200",
                ],
                "webhook": {"enabled": True, "path": "github", "secret": "s3"},
                "polling": {"enabled": False},
            }
        ),
    )

    response = await plugin._handle_webhook(
        _request(_payload(), event="issues", secret="s3")
    )
    await _wait_for_sent(api, 2)
    duplicate = await plugin._handle_webhook(
        _request(_payload(), event="issues", secret="s3")
    )

    assert response.status_code == 202
    assert duplicate.status_code == 204
    assert len(api.sent_messages) == 2
    assert api.sent_messages[0][0] == "100"
    assert api.sent_messages[0][2] == "milky"
    assert api.sent_messages[1][0] == "200"
    assert api.sent_messages[1][2] == "telegram"
    assert "🟢 GitHub Issue Opened\n📌 #123 Bug report" in api.sent_messages[0][1].text
    assert "👤 alice" in api.sent_messages[0][1].text
    assert (
        "🔗 https://github.com/octocat/hello/issues/123" in api.sent_messages[0][1].text
    )
    assert api.sent_messages[0][1].extra["chat_address"] == "milky:group:100"


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature() -> None:
    api = _API()
    plugin = GitHubNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "repo": "octocat/hello",
                "target_chat_addresses": ["milky:group:100"],
                "webhook": {"enabled": True, "secret": "s3"},
                "polling": {"enabled": False},
            }
        ),
    )

    response = await plugin._handle_webhook(
        _request(_payload(), secret="s3", signature="sha256=bad")
    )

    assert response.status_code == 403
    assert api.sent_messages == []


@pytest.mark.asyncio
async def test_webhook_ignores_ping_wrong_repo_and_unsupported_action() -> None:
    api = _API()
    plugin = GitHubNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "repo": "octocat/hello",
                "target_chat_addresses": ["milky:group:100"],
                "webhook": {"enabled": True},
                "polling": {"enabled": False},
            }
        ),
    )

    ping = await plugin._handle_webhook(
        _request({"zen": "hi"}, event="ping", delivery="d1")
    )
    wrong_repo = await plugin._handle_webhook(
        _request(_payload(repo="octocat/other"), event="issues", delivery="d2")
    )
    edited = await plugin._handle_webhook(
        _request(_payload(action="edited"), event="issues", delivery="d3")
    )
    await asyncio.sleep(0)

    assert ping.status_code == 204
    assert wrong_repo.status_code == 204
    assert edited.status_code == 204
    assert api.sent_messages == []


@pytest.mark.asyncio
async def test_dynamic_watch_and_unwatch_commands_update_targets() -> None:
    api = _API()
    plugin = GitHubNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "repo": "octocat/hello",
                "target_chat_addresses": ["telegram:private:200"],
                "webhook": {"enabled": True},
                "polling": {"enabled": False},
                "registration": {"enabled": True},
            }
        ),
    )
    inbound = InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="100",
        user_id="u1",
        text="/github_watch",
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="milky", chat_type="group"),
    )

    watched = await plugin._cmd_watch(args="", inbound=inbound, session_id="s")
    assert "Watching" in watched.message.text  # type: ignore[union-attr]
    assert await plugin._target_addresses() == [
        "telegram:private:200",
        "milky:group:100",
    ]

    unwatched = await plugin._cmd_unwatch(args="", inbound=inbound, session_id="s")
    assert "Stopped" in unwatched.message.text  # type: ignore[union-attr]
    assert await plugin._target_addresses() == ["telegram:private:200"]


@pytest.mark.asyncio
async def test_polling_first_run_baselines_then_reports_state_change() -> None:
    api = _API()
    plugin = GitHubNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "repo": "octocat/hello",
                "target_chat_addresses": ["milky:group:100"],
                "webhook": {"enabled": False},
                "polling": {"enabled": True},
            }
        ),
    )

    async def _first_items() -> list[_GitHubItem]:
        return [
            _GitHubItem(
                kind="issue",
                number=1,
                title="Bug",
                url="https://github.com/octocat/hello/issues/1",
                state="open",
                user="alice",
            )
        ]

    async def _second_items() -> list[_GitHubItem]:
        return [
            _GitHubItem(
                kind="issue",
                number=1,
                title="Bug",
                url="https://github.com/octocat/hello/issues/1",
                state="closed",
                user="alice",
            )
        ]

    plugin._fetch_polling_items = _first_items  # type: ignore[method-assign]
    await plugin._poll_once()
    assert api.sent_messages == []

    plugin._fetch_polling_items = _second_items  # type: ignore[method-assign]
    await plugin._poll_once()

    assert len(api.sent_messages) == 1
    assert "🔴 GitHub Issue Closed\n📌 #1 Bug" in api.sent_messages[0][1].text


@pytest.mark.asyncio
async def test_polling_does_not_repeat_state_written_by_webhook() -> None:
    api = _API()
    plugin = GitHubNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "repo": "octocat/hello",
                "target_chat_addresses": ["milky:group:100"],
                "webhook": {"enabled": True, "secret": "s3"},
                "polling": {"enabled": True},
            }
        ),
    )

    async def _open_items() -> list[_GitHubItem]:
        return [
            _GitHubItem(
                kind="issue",
                number=1,
                title="Bug",
                url="https://github.com/octocat/hello/issues/1",
                state="open",
                user="alice",
            )
        ]

    plugin._fetch_polling_items = _open_items  # type: ignore[method-assign]
    await plugin._poll_once()
    assert api.sent_messages == []

    response = await plugin._handle_webhook(
        _request(
            _payload(action="closed", number=1, title="Bug"),
            event="issues",
            delivery="delivery-close-1",
            secret="s3",
        )
    )
    await _wait_for_sent(api, 1)
    assert response.status_code == 202
    assert "🔴 GitHub Issue Closed\n📌 #1 Bug" in api.sent_messages[0][1].text

    async def _closed_items() -> list[_GitHubItem]:
        return [
            _GitHubItem(
                kind="issue",
                number=1,
                title="Bug",
                url="https://github.com/octocat/hello/issues/1",
                state="closed",
                user="alice",
            )
        ]

    plugin._fetch_polling_items = _closed_items  # type: ignore[method-assign]
    await plugin._poll_once()

    assert len(api.sent_messages) == 1
