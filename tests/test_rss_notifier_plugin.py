"""Tests for the external RSS notifier plugin."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "rss-notifier"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from nahida_bot_sdk import (  # noqa: E402
    ChatContext,
    InboundMessage,
    OutboundMessage,
    PluginManifest,
)
from nahida_bot_sdk.testing import RecordingMockBotAPI  # noqa: E402
from nahida_plugin_rss_notifier.plugin import (  # noqa: E402
    RSSNotifierPlugin,
    _FeedFetchResult,
    _FeedItem,
    _parse_feed,
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
        id="rss-notifier",
        name="RSS Notifier",
        version="0.1.0",
        entrypoint="nahida_plugin_rss_notifier.plugin:RSSNotifierPlugin",
        config=config,
    )


def _inbound() -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        platform="milky",
        chat_id="100",
        user_id="u1",
        text="/rss_sub https://example.com/feed.xml",
        raw_event={},
        is_group=True,
        chat_context=ChatContext(platform="milky", chat_type="group"),
    )


@pytest.mark.asyncio
async def test_dynamic_subscribe_list_and_unsubscribe_commands() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest({"feeds": [], "polling": {"enabled": False}}),
    )

    subscribed = await plugin._cmd_subscribe(
        args="https://example.com/feed.xml Example Feed",
        inbound=_inbound(),
        session_id="s",
    )
    listed = await plugin._cmd_list(args="", inbound=_inbound(), session_id="s")
    unsubscribed = await plugin._cmd_unsubscribe(
        args="1",
        inbound=_inbound(),
        session_id="s",
    )
    listed_after = await plugin._cmd_list(args="", inbound=_inbound(), session_id="s")

    assert "Subscribed milky:group:100" in subscribed.message.text  # type: ignore[union-attr]
    assert "Example Feed" in listed.message.text  # type: ignore[union-attr]
    assert "https://example.com/feed.xml" in listed.message.text  # type: ignore[union-attr]
    assert "Unsubscribed milky:group:100" in unsubscribed.message.text  # type: ignore[union-attr]
    assert "not subscribed" in listed_after.message.text  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_polling_first_run_baselines_then_reports_new_item() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [{"url": "https://example.com/feed.xml", "title": "News"}],
                "polling": {"enabled": False, "max_new_items_per_feed_per_poll": 5},
            }
        ),
    )

    async def _first_fetch(url: str) -> _FeedFetchResult:
        assert url == "https://example.com/feed.xml"
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="item-1",
                    title="Existing item",
                    link="https://example.com/1",
                ),
            ),
        )

    async def _second_fetch(url: str) -> _FeedFetchResult:
        assert url == "https://example.com/feed.xml"
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="item-2",
                    title="Fresh item",
                    link="https://example.com/2",
                    summary="A new item.",
                ),
                _FeedItem(
                    key="item-1",
                    title="Existing item",
                    link="https://example.com/1",
                ),
            ),
        )

    plugin._fetch_feed = _first_fetch  # type: ignore[method-assign]
    await plugin._poll_once()
    assert api.sent_messages == []

    plugin._fetch_feed = _second_fetch  # type: ignore[method-assign]
    await plugin._poll_once()

    assert len(api.sent_messages) == 1
    assert api.sent_messages[0][0] == "100"
    assert api.sent_messages[0][2] == "milky"
    assert "📰 News\n📌 Fresh item" in api.sent_messages[0][1].text
    assert "🔗 https://example.com/2" in api.sent_messages[0][1].text
    assert api.sent_messages[0][1].extra["chat_address"] == "milky:group:100"


def test_parse_rss_feed() -> None:
    result = _parse_feed(
        b"""
        <rss version="2.0">
          <channel>
            <title>Example RSS</title>
            <item>
              <guid>g1</guid>
              <title>First</title>
              <link>https://example.com/first</link>
              <pubDate>Sun, 21 Jun 2026 12:00:00 GMT</pubDate>
              <description><![CDATA[<p>Hello <b>world</b></p>]]></description>
            </item>
          </channel>
        </rss>
        """,
        max_items=10,
    )

    assert result.title == "Example RSS"
    assert result.items[0].key == "g1"
    assert result.items[0].title == "First"
    assert result.items[0].summary == "Hello world"


def test_parse_atom_feed() -> None:
    result = _parse_feed(
        b"""
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Example Atom</title>
          <entry>
            <id>tag:example.com,2026:first</id>
            <title>Atom First</title>
            <link href="https://example.com/atom-first" />
            <updated>2026-06-21T12:00:00Z</updated>
            <summary>Atom summary</summary>
          </entry>
        </feed>
        """,
        max_items=10,
    )

    assert result.title == "Example Atom"
    assert result.items[0].key == "tag:example.com,2026:first"
    assert result.items[0].title == "Atom First"
    assert result.items[0].link == "https://example.com/atom-first"


@pytest.mark.asyncio
async def test_on_enable_starts_managed_interval_task() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "feeds": [],
                "polling": {
                    "enabled": True,
                    "interval_seconds": 120,
                    "initial_delay_seconds": 3,
                },
            }
        ),
    )

    await plugin.on_enable()

    assert api.spawned_tasks["rss-poll"]["kind"] == "interval"
    assert api.spawned_tasks["rss-poll"]["interval_seconds"] == 120
    assert api.spawned_tasks["rss-poll"]["initial_delay"] == 3
