"""Tests for the GitHub channel plugin (webhook intake, routing, sends)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest

from nahida_bot.channels.github.client import GitHubClientError
from nahida_bot.channels.github.plugin import GitHubChannelPlugin
from nahida_bot.core.events import MessageObserved, MessageReceived
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.manifest import PluginManifest
from nahida_bot_sdk import WebhookRequest

from .helpers import RecordingMockBotAPI

pytestmark = pytest.mark.asyncio

REPO = "AI1379/nahida-bot"
BOT_LOGIN = "nahida-bot"
WEBHOOK_SECRET = "whsec-test"


class _FakeClient:
    def __init__(self, *, fail_identity: bool = False) -> None:
        self.identity_calls = 0
        self.fail_identity = fail_identity
        self.comments: list[dict[str, Any]] = []
        self.closed = False
        self.org_checks: list[tuple[str, str]] = []
        self.org_members: set[str] = set()
        self.fail_org_probe = False

    async def close(self) -> None:
        self.closed = True

    async def get_authenticated_user(self) -> dict[str, Any]:
        self.identity_calls += 1
        if self.fail_identity:
            raise GitHubClientError("api down")
        return {"login": BOT_LOGIN}

    async def check_org_membership(self, org: str, username: str) -> bool:
        self.org_checks.append((org, username))
        if self.fail_org_probe:
            raise GitHubClientError("concealed membership")
        return username.lower() in self.org_members

    async def add_comment(
        self, owner: str, repo: str, number: int, *, body: str
    ) -> dict[str, Any]:
        self.comments.append(
            {"owner": owner, "repo": repo, "number": number, "body": body}
        )
        return {
            "id": 4242,
            "html_url": f"https://github.com/{owner}/{repo}/issues/{number}#issuecomment-4242",
        }


def _manifest(**config_overrides: object) -> PluginManifest:
    config: dict[str, Any] = {
        "webhook_secret": WEBHOOK_SECRET,
    }
    config.update(config_overrides)
    return PluginManifest(
        id="github",
        name="GitHub Channel",
        version="0.1.0",
        entrypoint="nahida_bot.channels.github.plugin:GitHubChannelPlugin",
        config=config,
    )


def _plugin(
    **config_overrides: object,
) -> tuple[GitHubChannelPlugin, RecordingMockBotAPI, _FakeClient]:
    api = RecordingMockBotAPI()
    plugin = GitHubChannelPlugin(api, _manifest(**config_overrides))
    fake_client = _FakeClient()
    plugin._client = fake_client  # noqa: SLF001 - test injection
    return plugin, api, fake_client


async def _loaded_plugin(
    **config_overrides: object,
) -> tuple[GitHubChannelPlugin, RecordingMockBotAPI, _FakeClient]:
    plugin, api, client = _plugin(**config_overrides)
    await plugin.on_load()
    return plugin, api, client


def _comment_payload(
    *,
    body: str = "@nahida-bot please triage this",
    action: str = "created",
    login: str = "visitor",
) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"full_name": REPO},
        "sender": {"login": login, "type": "User"},
        "issue": {
            "number": 31,
            "id": 2222,
            "title": "feat: 接入 GitHub Bot",
            "body": "desc",
        },
        "comment": {"id": 9999, "body": body, "created_at": "2026-09-25T08:00:00Z"},
    }


def _request(
    payload: dict[str, Any],
    *,
    event: str = "issue_comment",
    delivery: str = "d-1",
    secret: str = WEBHOOK_SECRET,
    content_type: str = "application/json",
    body_override: bytes | None = None,
) -> WebhookRequest:
    body = body_override if body_override is not None else json.dumps(payload).encode()
    headers = {
        "content-type": content_type,
        "x-github-event": event,
        "x-github-delivery": delivery,
    }
    if secret:
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        headers["x-hub-signature-256"] = signature
    return WebhookRequest(
        method="POST", path="github", headers=headers, query={}, body=body
    )


async def _drain(plugin: GitHubChannelPlugin) -> None:
    if plugin._background_tasks:  # noqa: SLF001 - test hook
        await asyncio.gather(*plugin._background_tasks)


# ── lifecycle ────────────────────────────────────────────────────


async def test_on_load_registers_surfaces_and_discovers_identity() -> None:
    plugin, api, client = await _loaded_plugin()

    assert api.registered_channels == [plugin]
    assert "github" in api.registered_webhooks
    assert plugin.bot_login == BOT_LOGIN
    assert client.identity_calls == 1
    assert set(api.registered_tools) == {
        "github_list_issues",
        "github_get_issue",
        "github_create_issue",
        "github_add_comment",
        "github_update_issue",
    }
    assert "markdown_rendering" in api.registered_prompt_supplements


async def test_on_load_with_configured_login_skips_discovery() -> None:
    plugin, api, client = await _loaded_plugin(bot_login="override-bot")

    assert plugin.bot_login == "override-bot"
    assert client.identity_calls == 0


async def test_on_load_survives_identity_failure() -> None:
    api = RecordingMockBotAPI()
    plugin = GitHubChannelPlugin(api, _manifest())
    plugin._client = _FakeClient(fail_identity=True)  # noqa: SLF001
    await plugin.on_load()

    assert api.registered_channels == [plugin]
    assert plugin.bot_login == ""
    await plugin.on_disable()


async def test_on_disable_unregisters_webhook() -> None:
    plugin, api, client = await _loaded_plugin()
    assert "github" in api.registered_webhooks

    await plugin.on_disable()

    assert "github" not in api.registered_webhooks


# ── webhook intake ───────────────────────────────────────────────


async def test_webhook_ping_answered_without_secret() -> None:
    plugin, api, client = _plugin(webhook_secret="")
    await plugin.on_load()

    response = await plugin._handle_webhook(_request({}, event="ping", secret=""))

    assert response.status_code == 204
    await plugin.on_disable()


async def test_webhook_rejected_without_configured_secret() -> None:
    plugin, api, client = _plugin(webhook_secret="")
    await plugin.on_load()

    response = await plugin._handle_webhook(_request(_comment_payload(), secret=""))

    assert response.status_code == 403
    await plugin.on_disable()


async def test_webhook_rejected_on_bad_signature() -> None:
    plugin, api, client = await _loaded_plugin()

    response = await plugin._handle_webhook(
        _request(_comment_payload(), secret="wrong-secret")
    )

    assert response.status_code == 403
    await plugin.on_disable()


async def test_webhook_rejected_on_bad_content_type() -> None:
    plugin, api, client = await _loaded_plugin()

    response = await plugin._handle_webhook(
        _request(_comment_payload(), content_type="application/x-www-form-urlencoded")
    )

    assert response.status_code == 415
    await plugin.on_disable()


async def test_webhook_rejected_on_invalid_json() -> None:
    plugin, api, client = await _loaded_plugin()

    response = await plugin._handle_webhook(
        _request(_comment_payload(), body_override=b"{not json")
    )

    assert response.status_code == 400
    await plugin.on_disable()


async def test_webhook_accepted_and_event_published() -> None:
    plugin, api, client = await _loaded_plugin()

    response = await plugin._handle_webhook(_request(_comment_payload()))
    await _drain(plugin)

    assert response.status_code == 202
    assert len(api.published_events) == 1
    event = api.published_events[0]
    assert isinstance(event, MessageReceived)
    assert event.source == "github"
    assert event.payload.session_id == f"github:group:{REPO}#31"
    await plugin.on_disable()


async def test_webhook_duplicate_delivery_ignored() -> None:
    plugin, api, client = await _loaded_plugin()

    first = await plugin._handle_webhook(_request(_comment_payload(), delivery="d-1"))
    await _drain(plugin)
    second = await plugin._handle_webhook(_request(_comment_payload(), delivery="d-1"))
    await _drain(plugin)

    assert first.status_code == 202
    assert second.status_code == 204
    assert len(api.published_events) == 1
    await plugin.on_disable()


# ── inbound policy ───────────────────────────────────────────────


async def test_mention_publishes_received_in_mention_mode() -> None:
    plugin, api, client = await _loaded_plugin()

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageReceived)
    await plugin.on_disable()


async def test_non_mention_dropped_without_capture() -> None:
    plugin, api, client = await _loaded_plugin()

    await plugin.handle_inbound_event(
        {
            "event": "issue_comment",
            "payload": _comment_payload(body="no mention here"),
        }
    )

    assert api.published_events == []
    await plugin.on_disable()


async def test_non_mention_observed_with_capture() -> None:
    plugin, api, client = await _loaded_plugin(group_context_capture=True)

    await plugin.handle_inbound_event(
        {
            "event": "issue_comment",
            "payload": _comment_payload(body="no mention here"),
        }
    )

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageObserved)
    await plugin.on_disable()


async def test_lifecycle_event_always_observed() -> None:
    plugin, api, client = await _loaded_plugin()

    payload = _comment_payload(action="closed")
    del payload["comment"]
    payload["issue"]["state"] = "closed"
    await plugin.handle_inbound_event({"event": "issues", "payload": payload})

    assert len(api.published_events) == 1
    assert isinstance(api.published_events[0], MessageObserved)
    await plugin.on_disable()


async def test_unsupported_event_published_nothing() -> None:
    plugin, api, client = await _loaded_plugin()

    await plugin.handle_inbound_event(
        {"event": "star", "payload": _comment_payload(action="created")}
    )

    assert api.published_events == []
    await plugin.on_disable()


# ── outbound ─────────────────────────────────────────────────────


async def test_send_message_posts_comment() -> None:
    plugin, api, client = await _loaded_plugin()

    comment_id = await plugin.send_message(
        f"{REPO}#31", OutboundMessage(text="已经看过这个 issue 了")
    )

    assert comment_id == "4242"
    assert client.comments == [
        {
            "owner": "AI1379",
            "repo": "nahida-bot",
            "number": 31,
            "body": "已经看过这个 issue 了",
        }
    ]
    await plugin.on_disable()


async def test_send_message_resolves_chat_address_extra() -> None:
    plugin, api, client = await _loaded_plugin()

    await plugin.send_message(
        "ignored-target",
        OutboundMessage(
            text="via address",
            extra={"chat_address": f"github:group:{REPO}#60"},
        ),
    )

    assert client.comments[0]["number"] == 60
    await plugin.on_disable()


async def test_send_message_invalid_target_returns_empty() -> None:
    plugin, api, client = await _loaded_plugin()

    comment_id = await plugin.send_message(
        "not-a-github-target", OutboundMessage(text="hello")
    )

    assert comment_id == ""
    assert client.comments == []
    await plugin.on_disable()


async def test_send_message_reasoning_excluded_by_default() -> None:
    plugin, api, client = await _loaded_plugin()

    await plugin.send_message(
        f"{REPO}#31",
        OutboundMessage(text="answer", reasoning="secret chain of thought"),
    )

    assert client.comments[0]["body"] == "answer"
    await plugin.on_disable()


async def test_send_message_reasoning_included_when_enabled() -> None:
    plugin, api, client = await _loaded_plugin(include_reasoning=True)

    await plugin.send_message(
        f"{REPO}#31",
        OutboundMessage(text="answer", reasoning="chain"),
    )

    assert "[💭 思考过程]" in client.comments[0]["body"]
    assert "answer" in client.comments[0]["body"]
    await plugin.on_disable()


async def test_send_message_truncates_long_text() -> None:
    plugin, api, client = await _loaded_plugin(max_comment_length=256)

    await plugin.send_message(f"{REPO}#31", OutboundMessage(text="x" * 500))

    body = client.comments[0]["body"]
    assert len(body) <= 256
    assert body.endswith("(truncated)")
    await plugin.on_disable()


async def test_send_message_skips_attachments_without_crash() -> None:
    from nahida_bot.plugins.base import Attachment

    plugin, api, client = await _loaded_plugin()

    comment_id = await plugin.send_message(
        f"{REPO}#31",
        OutboundMessage(
            text="see logs",
            attachments=[Attachment(type="file", path="./log.txt")],
        ),
    )

    assert comment_id == "4242"
    assert client.comments[0]["body"] == "see logs"
    await plugin.on_disable()


async def test_send_message_empty_text_returns_empty() -> None:
    plugin, api, client = await _loaded_plugin()

    comment_id = await plugin.send_message(f"{REPO}#31", OutboundMessage(text="   "))

    assert comment_id == ""
    assert client.comments == []
    await plugin.on_disable()


# ── org membership filtering (allowed_orgs) ──────────────────────


async def test_org_member_passes_when_orgs_configured() -> None:
    plugin, api, client = await _loaded_plugin(allowed_orgs=["AI1379"])
    client.org_members = {"visitor"}

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert len(api.published_events) == 1
    assert client.org_checks == [("AI1379", "visitor")]
    await plugin.on_disable()


async def test_org_non_member_dropped() -> None:
    plugin, api, client = await _loaded_plugin(allowed_orgs=["AI1379"])
    client.org_members = {"someone-else"}

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert api.published_events == []
    assert client.org_checks == [("AI1379", "visitor")]
    await plugin.on_disable()


async def test_org_probe_failure_denies_sender_fail_closed() -> None:
    plugin, api, client = await _loaded_plugin(allowed_orgs=["AI1379"])
    client.fail_org_probe = True

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert api.published_events == []
    await plugin.on_disable()


async def test_org_membership_cached_across_events() -> None:
    plugin, api, client = await _loaded_plugin(allowed_orgs=["AI1379"])
    client.org_members = {"visitor"}

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )
    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert client.org_checks == [("AI1379", "visitor")]  # 只探测一次
    await plugin.on_disable()


async def test_static_allowlist_sender_skips_org_probe() -> None:
    plugin, api, client = await _loaded_plugin(
        allowed_senders=["visitor"], allowed_orgs=["AI1379"]
    )

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert len(api.published_events) == 1
    assert client.org_checks == []
    await plugin.on_disable()


async def test_orgs_empty_never_probes() -> None:
    plugin, api, client = await _loaded_plugin()

    await plugin.handle_inbound_event(
        {"event": "issue_comment", "payload": _comment_payload()}
    )

    assert len(api.published_events) == 1
    assert client.org_checks == []
    await plugin.on_disable()
