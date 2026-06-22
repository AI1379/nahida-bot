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
    Attachment,
    ChatContext,
    InboundMessage,
    OutboundMessage,
    PluginManifest,
)
from nahida_bot_sdk.testing import RecordingMockBotAPI  # noqa: E402
from nahida_plugin_rss_notifier.plugin import (  # noqa: E402
    FEED_STATE_KEY,
    RSSNotifierPlugin,
    _FeedFetchResult,
    _FeedItem,
    _FeedSubscription,
    _LatestEntry,
    _format_latest_entries,
    _format_notification,
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
async def test_on_load_registers_latest_command() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest({"registration": {"enabled": True}}),
    )

    await plugin.on_load()

    assert "rss_latest" in api.registered_commands
    assert "rss_recent" in api.registered_commands


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


@pytest.mark.asyncio
async def test_latest_lists_recent_items_without_mutating_poll_state() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [{"url": "https://example.com/feed.xml", "title": "News"}],
                "polling": {"enabled": False},
            }
        ),
    )

    async def _fetch(url: str) -> _FeedFetchResult:
        assert url == "https://example.com/feed.xml"
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="old",
                    title="Old item",
                    link="https://example.com/old",
                    published="Sun, 21 Jun 2026 04:00:00 GMT",
                    paragraphs=("Older paragraph.",),
                ),
                _FeedItem(
                    key="new",
                    title="New item",
                    link="https://example.com/new",
                    published="Sun, 21 Jun 2026 05:00:00 GMT",
                    paragraphs=("Newer paragraph.",),
                    image_urls=("https://example.com/new.png",),
                ),
            ),
        )

    plugin._fetch_feed = _fetch  # type: ignore[method-assign]
    result = await plugin._cmd_latest(args="1", inbound=_inbound(), session_id="s")

    assert result.message is not None
    assert "Latest 1 RSS item(s) for milky:group:100" in result.message.text
    assert "[News] New item" in result.message.text
    assert "https://example.com/new" in result.message.text
    assert "https://example.com/new.png" in result.message.text
    assert "Old item" not in result.message.text
    assert await api.plugin_data_get(FEED_STATE_KEY) is None


@pytest.mark.asyncio
async def test_latest_can_select_subscription_by_list_index() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [
                    {"url": "https://b.example/feed.xml", "title": "B Feed"},
                    {"url": "https://a.example/feed.xml", "title": "A Feed"},
                ],
                "polling": {"enabled": False},
            }
        ),
    )
    fetched: list[str] = []

    async def _fetch(url: str) -> _FeedFetchResult:
        fetched.append(url)
        return _FeedFetchResult(
            title=url,
            items=(
                _FeedItem(
                    key=url,
                    title=f"Item from {url}",
                    link=url,
                ),
            ),
        )

    plugin._fetch_feed = _fetch  # type: ignore[method-assign]
    result = await plugin._cmd_latest(args="3 1", inbound=_inbound(), session_id="s")

    assert fetched == ["https://a.example/feed.xml"]
    assert result.message is not None
    assert "[A Feed] Item from https://a.example/feed.xml" in result.message.text


@pytest.mark.asyncio
async def test_latest_can_select_subscription_by_display_name() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [
                    {"url": "https://b.example/feed.xml", "title": "B Feed"},
                    {"url": "https://a.example/feed.xml", "title": "原神 新闻"},
                ],
                "polling": {"enabled": False},
            }
        ),
    )
    fetched: list[str] = []

    async def _fetch(url: str) -> _FeedFetchResult:
        fetched.append(url)
        return _FeedFetchResult(
            title=url,
            items=(
                _FeedItem(
                    key=url,
                    title=f"Item from {url}",
                    link=url,
                ),
            ),
        )

    plugin._fetch_feed = _fetch  # type: ignore[method-assign]
    result = await plugin._cmd_latest(
        args="2 原神 新闻", inbound=_inbound(), session_id="s"
    )

    assert fetched == ["https://a.example/feed.xml"]
    assert result.message is not None
    assert "[原神 新闻] Item from https://a.example/feed.xml" in result.message.text


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


def test_parse_rss_feed_preserves_paragraphs_and_extracts_images() -> None:
    result = _parse_feed(
        b"""
        <rss version="2.0">
          <channel>
            <title>Example RSS</title>
            <item>
              <guid>g1</guid>
              <title>First</title>
              <link>https://example.com/posts/first</link>
              <description><![CDATA[
                <p><img src="/images/first.png" alt=""></p>
                <p>Hello <b>world</b></p>
                <p>Second<br>line</p>
              ]]></description>
            </item>
          </channel>
        </rss>
        """,
        max_items=10,
    )

    item = result.items[0]
    assert item.paragraphs == ("Hello world", "Second\nline")
    assert item.summary == "Hello world Second\nline"
    assert item.image_urls == ("https://example.com/images/first.png",)


def test_notification_rendering_preserves_line_breaks() -> None:
    text = _format_notification(
        feed_title="News",
        item=_FeedItem(
            key="item",
            title="Fresh item",
            link="https://example.com/item",
            paragraphs=("Line one\nLine two", "Next paragraph"),
        ),
    )

    assert "Line one\nLine two\n\nNext paragraph" in text


def test_latest_rendering_preserves_multiline_preview() -> None:
    text = _format_latest_entries(
        target="milky:group:100",
        entries=[
            _LatestEntry(
                feed_title="News",
                item=_FeedItem(
                    key="item",
                    title="Fresh item",
                    link="https://example.com/item",
                    paragraphs=("Line one\nLine two", "Next paragraph"),
                ),
                sequence=0,
            )
        ],
        failures=[],
        limit=1,
    )

    assert "   Line one\n   Line two\n\n   Next paragraph" in text


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
async def test_notify_downloads_image_attachment_and_cleans_temp_file(
    tmp_path: Path,
) -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "rendering": {
                    "send_image_attachments": True,
                    "max_images": 1,
                }
            }
        ),
    )
    image_path = tmp_path / "nahida-rss-test.png"
    image_path.write_bytes(b"png")

    async def _download(image_url: str, **kwargs: Any) -> Attachment:
        assert kwargs == {
            "feed_url": "https://example.com/feed.xml",
            "item_key": "item",
            "cleanup_after_send": False,
        }
        assert image_url == "https://example.com/image.png"
        return Attachment(
            type="photo",
            path=str(image_path),
            filename=image_path.name,
            mime_type="image/png",
        )

    plugin._download_image_attachment = _download  # type: ignore[method-assign]
    await plugin._notify(
        _FeedSubscription(
            key="feed",
            url="https://example.com/feed.xml",
            title="",
            targets=("milky:group:100",),
            source="test",
        ),
        _FeedItem(
            key="item",
            title="Fresh item",
            link="https://example.com/item",
            paragraphs=("Paragraph one.",),
            image_urls=("https://example.com/image.png",),
        ),
        feed_title="News",
    )

    assert len(api.sent_messages) == 1
    outbound = api.sent_messages[0][1]
    assert "🖼" not in outbound.text
    assert len(outbound.attachments) == 1
    assert outbound.attachments[0].type == "photo"
    assert outbound.attachments[0].path == str(image_path)
    assert image_path.exists()


@pytest.mark.asyncio
async def test_latest_returns_image_attachment_when_rendering_allows() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [{"url": "https://example.com/feed.xml", "title": "News"}],
                "polling": {"enabled": False},
                "rendering": {
                    "send_image_attachments": True,
                    "max_images": 1,
                },
            }
        ),
    )
    image_path = Path("managed-image.png")

    async def _fetch(url: str) -> _FeedFetchResult:
        assert url == "https://example.com/feed.xml"
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="item",
                    title="Fresh item",
                    link="https://example.com/item",
                    published="Sun, 21 Jun 2026 05:00:00 GMT",
                    paragraphs=("Paragraph one.",),
                    image_urls=("https://example.com/image.png",),
                ),
            ),
        )

    async def _download(image_url: str, **kwargs: Any) -> Attachment:
        assert image_url == "https://example.com/image.png"
        assert kwargs == {
            "feed_url": "https://example.com/item",
            "item_key": "item",
            "cleanup_after_send": True,
        }
        return Attachment(
            type="photo",
            path=str(image_path),
            filename=image_path.name,
            mime_type="image/png",
            extra={
                "managed_temp_file": True,
                "cleanup_token": "token",
                "cleanup_after_send": True,
            },
        )

    plugin._fetch_feed = _fetch  # type: ignore[method-assign]
    plugin._download_image_attachment = _download  # type: ignore[method-assign]
    result = await plugin._cmd_latest(args="1", inbound=_inbound(), session_id="s")

    assert result.message is not None
    assert "[News] Fresh item" in result.message.text
    assert len(result.message.attachments) == 1
    assert result.message.attachments[0].extra["managed_temp_file"] is True


@pytest.mark.asyncio
async def test_notify_falls_back_to_image_url_when_download_fails() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "rendering": {
                    "send_image_attachments": True,
                    "max_images": 1,
                }
            }
        ),
    )

    async def _download(image_url: str, **kwargs: Any) -> Attachment:
        del kwargs
        raise RuntimeError(f"cannot download {image_url}")

    plugin._download_image_attachment = _download  # type: ignore[method-assign]
    await plugin._notify(
        _FeedSubscription(
            key="feed",
            url="https://example.com/feed.xml",
            title="",
            targets=("milky:group:100",),
            source="test",
        ),
        _FeedItem(
            key="item",
            title="Fresh item",
            link="https://example.com/item",
            paragraphs=("Paragraph one.",),
            image_urls=("https://example.com/image.png",),
        ),
        feed_title="News",
    )

    outbound = api.sent_messages[0][1]
    assert outbound.attachments == []
    assert "🖼 https://example.com/image.png" in outbound.text


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
