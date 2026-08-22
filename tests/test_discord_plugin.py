"""Tests for DiscordPlugin."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from nahida_bot.channels.discord.config import DiscordPluginConfig
from nahida_bot.channels.discord.plugin import DiscordPlugin, _split_text
from nahida_bot.core.events import MessageObserved, MessageReceived
from nahida_bot.plugins.manifest import PluginManifest

from .helpers import RecordingMockBotAPI


class RateLimitError(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after}")
        self.retry_after = retry_after


def _make_manifest(**overrides: object) -> PluginManifest:
    config: dict[str, object] = {"bot_token": "test-token-123"}
    user_config = overrides.pop("config", {}) or {}
    if not isinstance(user_config, dict):
        raise TypeError("config override must be a mapping")
    config.update(user_config)
    defaults: dict[str, object] = {
        "id": "discord",
        "name": "Discord Channel",
        "version": "0.1.0",
        "entrypoint": "nahida_bot.channels.discord.plugin:DiscordPlugin",
    }
    defaults.update(overrides)
    return PluginManifest(config=config, **defaults)  # type: ignore[arg-type]


class FakeTransport:
    """Test double for DiscordTransport — no discord.py involved."""

    async def login(self) -> dict[str, str]:
        self.login_calls += 1
        return dict(self.login_result)

    async def start(self) -> None:
        await asyncio.sleep(3600)  # simulate a live gateway connection

    async def close(self) -> None:
        self.closed = True

    async def send_text(self, target: str, text: str, reply_to: str = "") -> str:
        if self.text_errors:
            raise self.text_errors.pop(0)
        self.sent_texts.append({"target": target, "text": text, "reply_to": reply_to})
        return str(1100 + len(self.sent_texts))

    async def send_file(
        self, target: str, path: str, filename: str = "", caption: str = ""
    ) -> str:
        self.sent_files.append(
            {
                "target": target,
                "path": path,
                "filename": filename,
                "caption": caption,
            }
        )
        return str(2000 + len(self.sent_files))

    async def fetch_user_info(self, user_id: str) -> dict[str, Any]:
        return {"id": user_id, "username": "someone"}

    async def fetch_channel_info(self, channel_id: str) -> dict[str, Any]:
        return {"id": channel_id, "name": "general", "type": "text"}

    # ── interaction / slash-command surface ──────────────

    def __init__(self) -> None:
        self.login_result: dict[str, str] = {"id": "999", "username": "nahidabot"}
        self.login_calls = 0
        self.closed = False
        self.sent_texts: list[dict[str, str]] = []
        self.sent_files: list[dict[str, str]] = []
        self.text_errors: list[Exception] = []
        self.deferred_interactions: list[str] = []
        self.autocomplete_responses: list[tuple[str, list[dict[str, str]]]] = []
        self.synced_commands: dict[str, list[dict[str, Any]]] = {}
        self.known_guilds: list[str] = []
        self.sync_error: Exception | None = None

    def guild_ids(self) -> list[str]:
        return list(self.known_guilds)

    async def sync_guild_commands(
        self, guild_id: str, payload: list[dict[str, Any]]
    ) -> None:
        if self.sync_error is not None:
            raise self.sync_error
        self.synced_commands[guild_id] = payload

    async def defer_interaction(self, interaction_object: Any) -> None:
        self.deferred_interactions.append(str(interaction_object.id))

    async def respond_autocomplete(
        self, interaction_object: Any, choices: list[dict[str, str]]
    ) -> None:
        self.autocomplete_responses.append((str(interaction_object.id), choices))


def _guild_event(content: str = "hello", **overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": "1001",
        "type": "default",
        "content": content,
        "timestamp": 1737000000.0,
        "author": {"id": "42", "name": "alice", "display_name": "Alice", "bot": False},
        "guild_id": "777",
        "channel": {
            "id": "111",
            "type": "text",
            "name": "general",
            "guild_id": "777",
            "parent_id": "",
        },
        "mentions": [],
        "mention_everyone": False,
        "attachments": [],
        "embed_count": 0,
        "reference_message_id": "",
    }
    message.update(overrides)
    return {"kind": "message", "message": message}


def _dm_event(content: str = "hello", **overrides: Any) -> dict[str, Any]:
    event = _guild_event(content, **overrides)
    message = event["message"]
    message["guild_id"] = ""
    message["channel"] = {
        "id": "500",
        "type": "dm",
        "name": "",
        "guild_id": "",
        "parent_id": "",
    }
    return event


def _thread_event(content: str = "hello", **overrides: Any) -> dict[str, Any]:
    event = _guild_event(content, **overrides)
    event["message"]["channel"] = {
        "id": "333",
        "type": "public_thread",
        "name": "big question",
        "guild_id": "777",
        "parent_id": "111",
    }
    return event


def _make_plugin(
    *, config: dict[str, object] | None = None, transport: FakeTransport | None = None
) -> tuple[RecordingMockBotAPI, DiscordPlugin]:
    api = RecordingMockBotAPI()
    plugin = DiscordPlugin(api=api, manifest=_make_manifest(config=config))
    fake = transport or FakeTransport()
    plugin._create_transport = lambda: fake  # type: ignore[method-assign]
    return api, plugin


async def _loaded_plugin(
    *, config: dict[str, object] | None = None
) -> tuple[RecordingMockBotAPI, DiscordPlugin, FakeTransport]:
    api, plugin = _make_plugin(config=config)
    await plugin.on_load()
    return api, plugin, plugin._transport  # type: ignore[return-value]


class TestDiscordPluginLifecycle:
    async def test_on_load_verifies_token_and_registers(self) -> None:
        api, plugin, transport = await _loaded_plugin()

        assert transport.login_calls == 1
        assert plugin.channel_id == "discord"
        assert api.registered_channels == [plugin]
        assert plugin._converter.bot_user_id == "999"

    async def test_on_load_raises_without_token(self) -> None:
        api = RecordingMockBotAPI()
        plugin = DiscordPlugin(
            api=api, manifest=_make_manifest(config={"bot_token": ""})
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISCORD_BOT_TOKEN", None)
            with pytest.raises(RuntimeError, match="bot_token not configured"):
                await plugin.on_load()

    async def test_reply_to_inbound_config_override(self) -> None:
        api, plugin = _make_plugin(config={"reply_to_inbound": False})
        assert plugin.reply_to_inbound is False

    async def test_reply_to_inbound_unset_uses_router_default(self) -> None:
        api, plugin = _make_plugin()
        assert plugin.reply_to_inbound is None

    async def test_on_enable_starts_gateway_and_tool(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        await plugin.on_enable()

        try:
            assert plugin._gateway_task is not None
            assert "download_media" in api.registered_tools
        finally:
            await plugin.on_disable()

        assert plugin._gateway_task is None
        assert transport.closed is True


class TestDiscordPluginInbound:
    async def test_guild_mention_publishes_received(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        event = _guild_event(
            content="<@999> help",
            mentions=[{"id": "999", "name": "nahidabot"}],
        )

        await plugin.handle_inbound_event(event)

        assert len(api.published_events) == 1
        published = api.published_events[0]
        assert isinstance(published, MessageReceived)
        assert published.source == "discord"
        assert published.payload.session_id == "discord:channel:111"

    async def test_guild_plain_message_dropped_in_mention_mode(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        await plugin.handle_inbound_event(_guild_event(content="just chatting"))

        assert api.published_events == []

    async def test_guild_context_capture_publishes_observed(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_context_capture": True})
        await plugin.handle_inbound_event(_guild_event(content="just chatting"))

        assert len(api.published_events) == 1
        assert isinstance(api.published_events[0], MessageObserved)
        assert api.published_events[0].payload.session_id == "discord:channel:111"

    async def test_dm_always_publishes(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        await plugin.handle_inbound_event(_dm_event(content="hi there"))

        assert len(api.published_events) == 1
        published = api.published_events[0]
        assert isinstance(published, MessageReceived)
        assert published.payload.session_id == "discord:private:500"
        assert published.payload.message.is_group is False

    async def test_thread_message_gets_thread_session(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_trigger_mode": "always"})
        await plugin.handle_inbound_event(_thread_event(content="thread talk"))

        assert len(api.published_events) == 1
        published = api.published_events[0]
        assert published.payload.session_id == "discord:thread:333"

    async def test_command_triggers_in_command_mode(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_trigger_mode": "command"})
        await plugin.handle_inbound_event(_guild_event(content="/help"))

        assert len(api.published_events) == 1

    async def test_bot_author_dropped(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_trigger_mode": "always"})
        event = _guild_event()
        event["message"]["author"] = {
            "id": "7",
            "name": "otherbot",
            "display_name": "OtherBot",
            "bot": True,
        }
        await plugin.handle_inbound_event(event)

        assert api.published_events == []

    async def test_system_message_type_dropped(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_trigger_mode": "always"})
        await plugin.handle_inbound_event(_guild_event(type="user_join"))

        assert api.published_events == []

    async def test_unknown_kind_ignored(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_trigger_mode": "always"})
        await plugin.handle_inbound_event({"kind": "reaction", "message": {}})

        assert api.published_events == []

    async def test_empty_text_dropped(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"group_trigger_mode": "always"})
        await plugin.handle_inbound_event(_guild_event(content=""))

        assert api.published_events == []

    async def test_attachment_only_message_publishes_with_marker(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        event = _dm_event(
            content="",
            attachments=[
                {
                    "id": "a1",
                    "filename": "cat.png",
                    "content_type": "image/png",
                    "size": 10,
                    "url": "https://cdn.example/a1",
                }
            ],
        )
        await plugin.handle_inbound_event(event)

        assert len(api.published_events) == 1
        assert "[Attachment: name=cat.png, type=image, id=a1]" in (
            api.published_events[0].payload.message.text
        )


class TestDiscordPluginGates:
    async def test_guild_not_allowed(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"allowed_guilds": ["888"]})
        await plugin.handle_inbound_event(_guild_event(content="hi"))

        assert api.published_events == []

    async def test_guild_allowed(self) -> None:
        api, plugin, _ = await _loaded_plugin(
            config={"allowed_guilds": ["777"], "group_trigger_mode": "always"}
        )
        await plugin.handle_inbound_event(_guild_event(content="hi"))

        assert len(api.published_events) == 1

    async def test_blocked_channel(self) -> None:
        api, plugin, _ = await _loaded_plugin(
            config={"group_trigger_mode": "always", "blocked_channels": ["111"]}
        )
        await plugin.handle_inbound_event(_guild_event(content="hi"))

        assert api.published_events == []

    async def test_dm_unaffected_by_guild_allowlist(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"allowed_guilds": ["888"]})
        await plugin.handle_inbound_event(_dm_event(content="hi"))

        assert len(api.published_events) == 1

    async def test_dm_user_not_allowed(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"allowed_dm_users": ["7"]})
        await plugin.handle_inbound_event(_dm_event(content="hi"))

        assert api.published_events == []

    async def test_dm_user_allowed(self) -> None:
        api, plugin, _ = await _loaded_plugin(config={"allowed_dm_users": ["42"]})
        await plugin.handle_inbound_event(_dm_event(content="hi"))

        assert len(api.published_events) == 1


class TestDiscordPluginOutbound:
    async def test_send_message_plain_text(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import OutboundMessage

        message_id = await plugin.send_message("111", OutboundMessage(text="hi"))

        assert message_id == "1101"
        assert transport.sent_texts == [{"target": "111", "text": "hi", "reply_to": ""}]

    async def test_send_message_splits_long_text(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import OutboundMessage

        long_text = "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(30))
        await plugin.send_message("111", OutboundMessage(text=long_text))

        assert len(transport.sent_texts) > 1
        assert all(len(item["text"]) <= 2000 for item in transport.sent_texts)

    async def test_reply_reference_only_on_first_chunk(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import OutboundMessage

        long_text = "y" * 4500
        await plugin.send_message(
            "111", OutboundMessage(text=long_text, reply_to="1001")
        )

        assert len(transport.sent_texts) == 3
        assert transport.sent_texts[0]["reply_to"] == "1001"
        assert transport.sent_texts[1]["reply_to"] == ""
        assert transport.sent_texts[2]["reply_to"] == ""

    async def test_reasoning_sent_as_blockquote_first(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import OutboundMessage

        await plugin.send_message(
            "111",
            OutboundMessage(text="answer", reasoning="because\nof reasons"),
        )

        assert len(transport.sent_texts) == 2
        assert transport.sent_texts[0]["text"].startswith("> ")
        assert "> because" in transport.sent_texts[0]["text"]
        assert "> of reasons" in transport.sent_texts[0]["text"]
        assert transport.sent_texts[1]["text"] == "answer"

    async def test_send_attachment(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import Attachment, OutboundMessage

        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"png")
            path = handle.name

        try:
            message_id = await plugin.send_message(
                "111",
                OutboundMessage(
                    text="see file",
                    attachments=[
                        Attachment(
                            type="image", path=path, filename="pic.png", caption="a pic"
                        )
                    ],
                ),
            )
        finally:
            os.unlink(path)

        assert message_id == "2001"
        assert transport.sent_files == [
            {
                "target": "111",
                "path": path,
                "filename": "pic.png",
                "caption": "a pic",
            }
        ]

    async def test_missing_attachment_skipped(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import Attachment, OutboundMessage

        message_id = await plugin.send_message(
            "111",
            OutboundMessage(
                text="see file",
                attachments=[Attachment(type="image", path="nope.png")],
            ),
        )

        assert transport.sent_files == []
        assert message_id == "1101"  # text chunk id

    async def test_rate_limit_retried(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import OutboundMessage

        transport.text_errors = [RateLimitError(0.01)]
        message_id = await plugin.send_message("111", OutboundMessage(text="hi"))

        assert message_id == "1101"
        assert len(transport.sent_texts) == 1

    async def test_rate_limit_gives_up_after_attempts(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        from nahida_bot.plugins.base import OutboundMessage

        transport.text_errors = [RateLimitError(0.01) for _ in range(5)]
        with pytest.raises(RateLimitError):
            await plugin.send_message("111", OutboundMessage(text="hi"))


class TestSplitText:
    def test_short_text_single_chunk(self) -> None:
        assert _split_text("hello", 2000) == ["hello"]

    def test_empty_text(self) -> None:
        assert _split_text("", 2000) == []

    def test_splits_on_paragraph_boundaries(self) -> None:
        text = "\n\n".join(["a" * 10] * 5)
        chunks = _split_text(text, 22)

        assert all(len(chunk) <= 22 for chunk in chunks)
        # Paragraph-boundary splitting is lossless when chunks re-join.
        assert "\n\n".join(chunks) == text

    def test_hard_splits_unbreakable_line(self) -> None:
        chunks = _split_text("x" * 500, 200)

        assert [len(chunk) for chunk in chunks] == [200, 200, 100]


class TestDiscordPluginInfo:
    async def test_get_user_info(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        info = await plugin.get_user_info("42")

        assert info["id"] == "42"
        assert info["username"] == "someone"

    async def test_get_group_info(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        info = await plugin.get_group_info("111")

        assert info["name"] == "general"

    async def test_info_without_transport_returns_empty(self) -> None:
        api, plugin = _make_plugin()
        assert await plugin.get_user_info("42") == {}
        assert await plugin.get_group_info("111") == {}


class TestDiscordPluginDownload:
    async def test_unknown_attachment_returns_none(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        assert await plugin.download_media("nope") is None

    async def test_download_via_legacy_dir(self, tmp_path: Path) -> None:
        api, plugin, _ = await _loaded_plugin(
            config={"media_download_dir": str(tmp_path)}
        )
        event = _dm_event(
            attachments=[
                {
                    "id": "a1",
                    "filename": "cat.png",
                    "content_type": "image/png",
                    "size": 4,
                    "url": "https://cdn.example/a1",
                }
            ]
        )
        await plugin.handle_inbound_event(event)

        async def fake_fetch(url: str) -> bytes:
            return b"DATA"

        plugin._fetch_attachment_bytes = fake_fetch  # type: ignore[method-assign]
        result = await plugin.download_media("a1")

        assert result is not None
        assert result.file_name.endswith(".png")
        assert result.file_size == 4
        assert Path(result.path).read_bytes() == b"DATA"

    async def test_download_via_media_store(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        event = _dm_event(
            attachments=[
                {
                    "id": "a1",
                    "filename": "cat.png",
                    "content_type": "image/png",
                    "size": 4,
                    "url": "https://cdn.example/a1",
                }
            ]
        )
        await plugin.handle_inbound_event(event)

        store = FakeMediaStore()
        plugin._media_store = lambda: store  # type: ignore[method-assign]

        async def fake_fetch(url: str) -> bytes:
            return b"DATA"

        plugin._fetch_attachment_bytes = fake_fetch  # type: ignore[method-assign]
        result = await plugin.download_media("a1")

        assert result is not None
        assert store.created_keys == ["discord:discord:a1"]
        assert Path(result.path).name == "discord_discord_a1.png"

        # Second download hits the cache without refetching.
        async def exploding_fetch(url: str) -> bytes:
            raise AssertionError("should not refetch")

        plugin._fetch_attachment_bytes = exploding_fetch  # type: ignore[method-assign]
        again = await plugin.download_media("a1")
        assert again is not None
        assert again.path == result.path

    async def test_download_tool_registered(self) -> None:
        api, plugin, _ = await _loaded_plugin()
        await plugin.on_enable()
        try:
            assert "download_media" in api.registered_tools
        finally:
            await plugin.on_disable()


class FakeMediaStore:
    def __init__(self) -> None:
        self.entries: dict[str, SimpleNamespace] = {}
        self.created_keys: list[str] = []

    async def get_entry(self, cache_key: str) -> SimpleNamespace | None:
        return self.entries.get(cache_key)

    async def get_or_create(self, cache_key: str, loader: Any) -> SimpleNamespace:
        self.created_keys.append(cache_key)
        if cache_key not in self.entries:
            payload = await loader()
            self.entries[cache_key] = SimpleNamespace(
                path=f"/cache/{cache_key.replace(':', '_')}{payload.suffix}",
                file_name=payload.file_name,
                mime_type=payload.mime_type,
                file_size=payload.file_size,
            )
        return self.entries[cache_key]


class TestDiscordConfigExposure:
    def test_config_property(self) -> None:
        api, plugin = _make_plugin()
        assert isinstance(plugin.config, DiscordPluginConfig)
        assert plugin.config.group_trigger_mode == "mention"


def _interaction_event(
    interaction_type: int = 2,
    command: str = "model",
    options: list[dict[str, Any]] | None = None,
    *,
    channel_id: str = "111",
    guild_id: str = "777",
) -> dict[str, Any]:
    obj = SimpleNamespace(id=5001, token="interaction-token")
    return {
        "kind": "interaction",
        "interaction": {
            "id": "5001",
            "type": interaction_type,
            "command_name": command,
            "options": options or [],
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user": {
                "id": "42",
                "name": "alice",
                "display_name": "Alice",
                "bot": False,
            },
            "token": "interaction-token",
            "_object": obj,
        },
    }


class TestDiscordSlashCommandInvocation:
    async def test_command_interaction_publishes_with_bot_mention(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        await plugin.handle_inbound_event(
            _interaction_event(
                options=[{"name": "name", "value": "deepseek-main", "focused": False}]
            )
        )

        assert transport.deferred_interactions == ["5001"]
        assert len(api.published_events) == 1
        published = api.published_events[0]
        assert isinstance(published, MessageReceived)
        # Options re-joined into the freeform args string.
        assert published.payload.message.text == "/model deepseek-main"
        # Explicit invocation counts as addressing the bot → passes mention mode.
        assert published.payload.message.mentions_bot is True
        assert published.payload.session_id == "discord:channel:111"

    async def test_dm_command_interaction_uses_private_session(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        await plugin.handle_inbound_event(
            _interaction_event(guild_id="", channel_id="500")
        )

        assert len(api.published_events) == 1
        assert api.published_events[0].payload.session_id == "discord:private:500"
        assert api.published_events[0].payload.message.is_group is False

    async def test_interaction_respects_guild_allowlist(self) -> None:
        api, plugin, transport = await _loaded_plugin(
            config={"allowed_guilds": ["888"]}
        )
        await plugin.handle_inbound_event(_interaction_event())

        assert api.published_events == []

    async def test_interaction_without_name_ignored(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        await plugin.handle_inbound_event(_interaction_event(command="", options=[]))

        assert api.published_events == []
        assert transport.deferred_interactions == []

    async def test_unsupported_interaction_type_ignored(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        await plugin.handle_inbound_event(_interaction_event(interaction_type=3))

        assert api.published_events == []
        assert transport.autocomplete_responses == []


class TestDiscordAutocomplete:
    async def test_autocomplete_responds_with_choices(self) -> None:
        from nahida_bot_sdk.commands import CompletionChoice

        api, plugin, transport = await _loaded_plugin()
        api.completion_results = [
            CompletionChoice(
                value="deepseek-main", display="deepseek-main", description="deepseek"
            ),
            CompletionChoice(value="gpt-4o"),
        ]
        await plugin.handle_inbound_event(
            _interaction_event(
                interaction_type=4,
                options=[
                    {"name": "name", "value": "deep", "focused": True},
                    {"name": "other", "value": "x", "focused": False},
                ],
            )
        )

        assert len(transport.autocomplete_responses) == 1
        interaction_id, choices = transport.autocomplete_responses[0]
        assert interaction_id == "5001"
        assert choices == [
            {"name": "deepseek-main", "value": "deepseek-main"},
            {"name": "gpt-4o", "value": "gpt-4o"},
        ]

    async def test_autocomplete_truncates_to_25(self) -> None:
        from nahida_bot_sdk.commands import CompletionChoice

        api, plugin, transport = await _loaded_plugin()
        api.completion_results = [CompletionChoice(value=f"m{i}") for i in range(40)]
        await plugin.handle_inbound_event(
            _interaction_event(
                interaction_type=4,
                options=[{"name": "name", "value": "", "focused": True}],
            )
        )

        assert len(transport.autocomplete_responses) == 1
        assert len(transport.autocomplete_responses[0][1]) == 25

    async def test_autocomplete_without_focused_option_ignored(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        await plugin.handle_inbound_event(
            _interaction_event(interaction_type=4, options=[])
        )

        assert transport.autocomplete_responses == []

    async def test_slow_completion_returns_empty_not_error(self) -> None:
        api, plugin, transport = await _loaded_plugin()

        async def slow_complete(query: Any) -> list[Any]:
            await asyncio.sleep(5)
            return []

        api.complete_command_argument = slow_complete  # type: ignore[method-assign]
        await plugin.handle_inbound_event(
            _interaction_event(
                interaction_type=4,
                options=[{"name": "name", "value": "", "focused": True}],
            )
        )

        assert transport.autocomplete_responses == [("5001", [])]


class TestDiscordCommandSync:
    async def test_ready_event_syncs_guild_commands(self) -> None:
        from nahida_bot_sdk.commands import CommandArgument, CommandInfo

        api, plugin, transport = await _loaded_plugin()
        transport.known_guilds = ["777", "778"]
        api.command_infos = [
            CommandInfo(
                name="model",
                description="List or switch model",
                aliases=(),
                plugin_id="builtin",
                arguments=[
                    CommandArgument(
                        name="name", description="model", completer=None, choices=("a",)
                    )
                ],
            ),
            CommandInfo(
                name="NotDiscordSafe",  # uppercase → skipped
                description="x",
                aliases=(),
                plugin_id="builtin",
            ),
        ]

        await plugin.handle_inbound_event({"kind": "ready"})

        assert set(transport.synced_commands) == {"777", "778"}
        payload = transport.synced_commands["777"]
        assert [cmd["name"] for cmd in payload] == ["model"]
        model = payload[0]
        assert model["options"][0]["autocomplete"] is True
        assert model["options"][0]["type"] == 3

    async def test_sync_respects_allowed_guilds(self) -> None:
        api, plugin, transport = await _loaded_plugin(
            config={"allowed_guilds": ["888"]}
        )
        transport.known_guilds = ["777", "888"]

        await plugin.handle_inbound_event({"kind": "ready"})

        assert set(transport.synced_commands) == {"888"}

    async def test_sync_disabled_by_config(self) -> None:
        api, plugin, transport = await _loaded_plugin(
            config={"register_slash_commands": False}
        )

        await plugin.handle_inbound_event({"kind": "ready"})

        assert transport.synced_commands == {}

    async def test_sync_failure_does_not_raise(self) -> None:
        api, plugin, transport = await _loaded_plugin()
        transport.sync_error = RuntimeError("rate limited")

        await plugin.handle_inbound_event({"kind": "ready"})  # must not raise
