"""Tests for the external RSS notifier plugin."""

from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta
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
async def test_polling_flood_after_feed_recovery_only_notifies_recent_items() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [{"url": "https://example.com/feed.xml", "title": "News"}],
                "polling": {
                    "enabled": False,
                    "interval_seconds": 10,
                    "max_new_items_per_feed_per_poll": 3,
                    "max_new_items_age_seconds": 10,
                },
            }
        ),
    )

    now = datetime.now(UTC)
    old = now - timedelta(minutes=30)

    def _pub_date(value: datetime) -> str:
        return value.strftime("%a, %d %b %Y %H:%M:%S GMT")

    baseline_items = (
        _FeedItem(
            key="item-0",
            title="Known item",
            link="https://example.com/0",
            published=_pub_date(old),
        ),
    )

    async def _baseline_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(title="News", items=baseline_items)

    async def _flood_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(
            title="News",
            items=(
                *(
                    _FeedItem(
                        key=f"stale-{i}",
                        title=f"Stale item {i}",
                        link=f"https://example.com/stale/{i}",
                        published=_pub_date(old),
                    )
                    for i in range(1, 7)
                ),
                _FeedItem(
                    key="item-0",
                    title="Known item",
                    link="https://example.com/0",
                    published=_pub_date(old),
                ),
                _FeedItem(
                    key="item-recent",
                    title="Fresh item",
                    link="https://example.com/recent",
                    published=_pub_date(now),
                ),
            ),
        )

    plugin._fetch_feed = _baseline_fetch  # type: ignore[method-assign]
    await plugin._poll_once()
    assert api.sent_messages == []

    plugin._fetch_feed = _flood_fetch  # type: ignore[method-assign]
    await plugin._poll_once()

    assert len(api.sent_messages) == 1
    assert "Fresh item" in api.sent_messages[0][1].text
    assert all("Stale item" not in sent[1].text for sent in api.sent_messages)


@pytest.mark.asyncio
async def test_polling_skips_new_items_published_outside_window() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [{"url": "https://example.com/feed.xml", "title": "News"}],
                "polling": {
                    "enabled": False,
                    "interval_seconds": 10,
                    "max_new_items_per_feed_per_poll": 3,
                    "max_new_items_age_seconds": 10,
                },
            }
        ),
    )

    now = datetime.now(UTC)
    old = now - timedelta(minutes=30)

    def _pub_date(value: datetime) -> str:
        return value.strftime("%a, %d %b %Y %H:%M:%S GMT")

    baseline_items = (
        _FeedItem(
            key="item-0",
            title="Known item",
            link="https://example.com/0",
            published=_pub_date(old),
        ),
    )

    async def _baseline_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(title="News", items=baseline_items)

    async def _stale_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="stale-1",
                    title="Stale item 1",
                    link="https://example.com/stale/1",
                    published=_pub_date(old),
                ),
                _FeedItem(
                    key="stale-2",
                    title="Stale item 2",
                    link="https://example.com/stale/2",
                    published=_pub_date(old),
                ),
                _FeedItem(
                    key="item-0",
                    title="Known item",
                    link="https://example.com/0",
                    published=_pub_date(old),
                ),
            ),
        )

    async def _fresh_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="fresh-1",
                    title="Fresh item",
                    link="https://example.com/fresh/1",
                    published=_pub_date(now),
                ),
                _FeedItem(
                    key="item-0",
                    title="Known item",
                    link="https://example.com/0",
                    published=_pub_date(old),
                ),
            ),
        )

    plugin._fetch_feed = _baseline_fetch  # type: ignore[method-assign]
    await plugin._poll_once()
    assert api.sent_messages == []

    plugin._fetch_feed = _stale_fetch  # type: ignore[method-assign]
    await plugin._poll_once()
    assert api.sent_messages == []

    plugin._fetch_feed = _fresh_fetch  # type: ignore[method-assign]
    await plugin._poll_once()

    assert len(api.sent_messages) == 1
    assert "Fresh item" in api.sent_messages[0][1].text


@pytest.mark.asyncio
async def test_polling_keeps_items_without_published_time() -> None:
    api = _API()
    plugin = RSSNotifierPlugin(
        api=api,
        manifest=_manifest(
            {
                "target_chat_addresses": ["milky:group:100"],
                "feeds": [{"url": "https://example.com/feed.xml", "title": "News"}],
                "polling": {
                    "enabled": False,
                    "interval_seconds": 10,
                    "max_new_items_per_feed_per_poll": 3,
                    "max_new_items_age_seconds": 10,
                },
            }
        ),
    )

    async def _baseline_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(
            title="News",
            items=(_FeedItem(key="item-0", title="Known item"),),
        )

    async def _flood_fetch(url: str) -> _FeedFetchResult:
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(key="no-date-1", title="No date item 1"),
                _FeedItem(key="no-date-2", title="No date item 2"),
                _FeedItem(key="no-date-3", title="No date item 3"),
                _FeedItem(key="item-0", title="Known item"),
            ),
        )

    plugin._fetch_feed = _baseline_fetch  # type: ignore[method-assign]
    await plugin._poll_once()

    plugin._fetch_feed = _flood_fetch  # type: ignore[method-assign]
    await plugin._poll_once()

    assert len(api.sent_messages) == 3
    assert {sent[1].text.splitlines()[1] for sent in api.sent_messages} == {
        "📌 No date item 1",
        "📌 No date item 2",
        "📌 No date item 3",
    }


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
                "rendering": {"send_image_attachments": False},
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

    assert result.suppress_response is True
    assert len(api.sent_messages) == 1
    outbound = api.sent_messages[0][1]
    assert "📰 News\n📌 New item" in outbound.text
    assert "https://example.com/new" in outbound.text
    assert "🖼 https://example.com/new.png" in outbound.text
    assert "Old item" not in outbound.text
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
    assert result.suppress_response is True
    assert len(api.sent_messages) == 1
    assert (
        "📰 A Feed\n📌 Item from https://a.example/feed.xml"
        in api.sent_messages[0][1].text
    )


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
    assert result.suppress_response is True
    assert len(api.sent_messages) == 1
    assert (
        "📰 原神 新闻\n📌 Item from https://a.example/feed.xml"
        in api.sent_messages[0][1].text
    )


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
            "feed_url": "https://example.com/feed.xml",
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

    assert result.suppress_response is True
    assert len(api.sent_messages) == 1
    outbound = api.sent_messages[0][1]
    assert "📰 News\n📌 Fresh item" in outbound.text
    assert len(outbound.attachments) == 1
    assert outbound.attachments[0].extra["managed_temp_file"] is True


@pytest.mark.asyncio
async def test_latest_sends_each_item_with_its_own_image_attachment() -> None:
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

    async def _fetch(url: str) -> _FeedFetchResult:
        assert url == "https://example.com/feed.xml"
        return _FeedFetchResult(
            title="News",
            items=(
                _FeedItem(
                    key="item-1",
                    title="First item",
                    link="https://example.com/1",
                    published="Sun, 21 Jun 2026 04:00:00 GMT",
                    paragraphs=("First paragraph.",),
                    image_urls=("https://example.com/1.png",),
                ),
                _FeedItem(
                    key="item-2",
                    title="Second item",
                    link="https://example.com/2",
                    published="Sun, 21 Jun 2026 05:00:00 GMT",
                    paragraphs=("Second paragraph.",),
                    image_urls=("https://example.com/2.png",),
                ),
            ),
        )

    async def _download(image_url: str, **kwargs: Any) -> Attachment:
        assert kwargs["feed_url"] == "https://example.com/feed.xml"
        assert kwargs["cleanup_after_send"] is True
        return Attachment(
            type="photo",
            path=f"{image_url.rsplit('/', 1)[-1]}",
            filename=image_url.rsplit("/", 1)[-1],
            mime_type="image/png",
        )

    plugin._fetch_feed = _fetch  # type: ignore[method-assign]
    plugin._download_image_attachment = _download  # type: ignore[method-assign]
    result = await plugin._cmd_latest(args="2", inbound=_inbound(), session_id="s")

    assert result.suppress_response is True
    assert len(api.sent_messages) == 2
    assert "Second item" in api.sent_messages[0][1].text
    assert api.sent_messages[0][1].attachments[0].filename == "2.png"
    assert "First item" in api.sent_messages[1][1].text
    assert api.sent_messages[1][1].attachments[0].filename == "1.png"


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
