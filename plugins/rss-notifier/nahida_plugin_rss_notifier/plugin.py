"""RSS/Atom notifier plugin for nahida-bot."""

from __future__ import annotations

import hashlib
import html
import re
import shlex
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from nahida_bot_sdk import ChatAddress, CommandResult, InboundMessage, OutboundMessage
from nahida_bot_sdk import Plugin
from pydantic import BaseModel, ConfigDict, Field, field_validator

DYNAMIC_SUBSCRIPTIONS_KEY = "dynamic_subscriptions"
FEED_STATE_KEY = "feed_state"


class RSSFeedConfig(BaseModel):
    """Static feed subscription declared in plugin config."""

    model_config = ConfigDict(extra="allow")

    url: str
    title: str = ""
    target_chat_addresses: list[str] = Field(default_factory=list)

    @field_validator("url", "title")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("target_chat_addresses")
    @classmethod
    def _strip_targets(cls, value: list[str]) -> list[str]:
        return _dedupe([item.strip() for item in value if item.strip()])


class RSSPollingConfig(BaseModel):
    """Polling behavior."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    interval_seconds: int = Field(default=300, ge=10)
    initial_delay_seconds: float = Field(default=10.0, ge=0.0)
    timeout_seconds: float = Field(default=20.0, ge=1.0)
    max_feed_bytes: int = Field(default=1_000_000, ge=1024)
    max_items_per_feed: int = Field(default=20, ge=1)
    max_known_items_per_feed: int = Field(default=200, ge=1)
    max_new_items_per_feed_per_poll: int = Field(default=5, ge=1)
    user_agent: str = "nahida-bot-rss-notifier/0.1"

    @field_validator("user_agent")
    @classmethod
    def _strip_user_agent(cls, value: str) -> str:
        value = value.strip()
        return value or "nahida-bot-rss-notifier/0.1"


class RSSRegistrationConfig(BaseModel):
    """Runtime command registration behavior."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class RSSNotifierConfig(BaseModel):
    """Plugin configuration."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    target_chat_addresses: list[str] = Field(default_factory=list)
    feeds: list[RSSFeedConfig] = Field(default_factory=list)
    polling: RSSPollingConfig = Field(default_factory=RSSPollingConfig)
    registration: RSSRegistrationConfig = Field(default_factory=RSSRegistrationConfig)

    @field_validator("target_chat_addresses")
    @classmethod
    def _strip_targets(cls, value: list[str]) -> list[str]:
        return _dedupe([item.strip() for item in value if item.strip()])


@dataclass(slots=True, frozen=True)
class _FeedSubscription:
    key: str
    url: str
    title: str
    targets: tuple[str, ...]
    source: str


@dataclass(slots=True, frozen=True)
class _FeedItem:
    key: str
    title: str
    link: str = ""
    published: str = ""
    summary: str = ""


@dataclass(slots=True, frozen=True)
class _FeedFetchResult:
    title: str
    items: tuple[_FeedItem, ...]


class RSSNotifierPlugin(Plugin):
    """Poll configured RSS/Atom feeds and push new items to chat addresses."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = RSSNotifierConfig.model_validate(manifest.config or {})

    async def on_load(self) -> None:
        if not self._config.enabled:
            self.api.logger.info("rss_notifier.disabled_by_config")
            return

        if self._config.registration.enabled:
            self.api.register_command(
                "rss_sub",
                self._cmd_subscribe,
                description=(
                    "Subscribe the current chat to an RSS/Atom feed. "
                    "Usage: /rss_sub <url> [display title]"
                ),
            )
            self.api.register_command(
                "rss_unsub",
                self._cmd_unsubscribe,
                description=(
                    "Unsubscribe the current chat from an RSS/Atom feed. "
                    "Usage: /rss_unsub <url-or-list-index>"
                ),
            )
            self.api.register_command(
                "rss_list",
                self._cmd_list,
                description="List RSS/Atom feeds watched by the current chat.",
            )
            self.api.register_command(
                "rss_poll",
                self._cmd_poll,
                description="Run one RSS/Atom polling pass now.",
            )

        self.api.logger.info(
            "rss_notifier.loaded",
            enabled=self._config.enabled,
            configured_feeds=len(self._config.feeds),
            default_targets=len(self._config.target_chat_addresses),
            registration_enabled=self._config.registration.enabled,
            polling_enabled=self._config.polling.enabled,
            polling_interval_seconds=self._config.polling.interval_seconds,
        )

    async def on_enable(self) -> None:
        if not self._config.enabled or not self._config.polling.enabled:
            return
        self.api.spawn_interval_task(
            "rss-poll",
            self._poll_once_safely,
            interval_seconds=self._config.polling.interval_seconds,
            initial_delay=self._config.polling.initial_delay_seconds,
        )
        self.api.logger.info(
            "rss_notifier.polling_started",
            interval_seconds=self._config.polling.interval_seconds,
            initial_delay_seconds=self._config.polling.initial_delay_seconds,
        )

    async def on_disable(self) -> None:
        self.api.cancel_task("rss-poll")

    async def _cmd_subscribe(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        try:
            parts = shlex.split(args)
        except ValueError as exc:
            return CommandResult.text(f"Invalid arguments: {exc}")
        if not parts:
            return CommandResult.text("Usage: /rss_sub <url> [display title]")

        url = _normalize_url(parts[0])
        if not _is_supported_feed_url(url):
            return CommandResult.text("Feed URL must be an http(s) URL.")

        title = " ".join(parts[1:]).strip()
        address = _address_from_inbound(inbound)
        target = address.chat_key
        data = await self._load_dynamic_data()
        feeds = data.setdefault("feeds", {})
        feed_key = _feed_key(url)
        record = feeds.get(feed_key)
        if not isinstance(record, dict):
            record = {"url": url, "title": title, "targets": []}
            feeds[feed_key] = record
        record["url"] = url
        if title:
            record["title"] = title
        targets = [str(item) for item in record.get("targets", [])]
        if target not in targets:
            targets.append(target)
        record["targets"] = _dedupe(targets)
        await self._store_dynamic_data(data)

        label = str(record.get("title") or url)
        return CommandResult.text(f"Subscribed {target} to RSS feed: {label}")

    async def _cmd_unsubscribe(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        token = args.strip()
        if not token:
            return CommandResult.text("Usage: /rss_unsub <url-or-list-index>")

        target = _address_from_inbound(inbound).chat_key
        current = await self._subscriptions_for_target(target)
        if not current:
            return CommandResult.text("This chat is not subscribed to any RSS feeds.")

        selected: _FeedSubscription | None = None
        if token.isdigit():
            index = int(token)
            if 1 <= index <= len(current):
                selected = current[index - 1]
        else:
            normalized = _normalize_url(token)
            selected = next((sub for sub in current if sub.url == normalized), None)

        if selected is None:
            return CommandResult.text("No matching RSS subscription found.")

        config_map = self._configured_subscription_map()
        configured = config_map.get(selected.key)
        if configured is not None and target in configured.targets:
            return CommandResult.text(
                "That subscription is declared in config.yaml; remove it from "
                "rss-notifier.feeds or target_chat_addresses there."
            )

        data = await self._load_dynamic_data()
        feeds = data.setdefault("feeds", {})
        record = feeds.get(selected.key)
        if not isinstance(record, dict):
            return CommandResult.text("No dynamic RSS subscription found to remove.")
        targets = [str(item) for item in record.get("targets", [])]
        record["targets"] = [item for item in targets if item != target]
        if not record["targets"]:
            feeds.pop(selected.key, None)
        await self._store_dynamic_data(data)

        return CommandResult.text(
            f"Unsubscribed {target} from RSS feed: {selected.url}"
        )

    async def _cmd_list(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del args, session_id
        target = _address_from_inbound(inbound).chat_key
        subscriptions = await self._subscriptions_for_target(target)
        if not subscriptions:
            return CommandResult.text("This chat is not subscribed to any RSS feeds.")

        lines = [f"RSS subscriptions for {target}:"]
        for index, sub in enumerate(subscriptions, start=1):
            label = sub.title or sub.url
            lines.append(f"{index}. {label}\n   {sub.url}")
        return CommandResult.text("\n".join(lines))

    async def _cmd_poll(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del args, inbound, session_id
        await self._poll_once_safely()
        return CommandResult.text("RSS polling pass completed.")

    async def _poll_once_safely(self) -> None:
        try:
            await self._poll_once()
        except Exception as exc:  # noqa: BLE001
            self.api.logger.exception("rss_notifier.poll_failed", error=str(exc))

    async def _poll_once(self) -> None:
        subscriptions = await self._subscription_map()
        if not subscriptions:
            self.api.logger.debug("rss_notifier.poll_skipped_no_subscriptions")
            return

        states = await self._load_feed_state()
        for sub in subscriptions.values():
            if not sub.targets:
                continue
            try:
                result = await self._fetch_feed(sub.url)
            except Exception as exc:  # noqa: BLE001
                self.api.logger.exception(
                    "rss_notifier.feed_fetch_failed",
                    url=sub.url,
                    error=str(exc),
                )
                continue

            state = states.get(sub.key)
            if not isinstance(state, dict):
                state = {}
                states[sub.key] = state
            known = state.setdefault("items", {})
            if not isinstance(known, dict):
                known = {}
                state["items"] = known
            initialized = bool(state.get("initialized"))

            new_items = [item for item in result.items if item.key not in known]
            if initialized and new_items:
                limit = self._config.polling.max_new_items_per_feed_per_poll
                for item in reversed(new_items[:limit]):
                    await self._notify(sub, item, feed_title=result.title)

            for item in result.items:
                known[item.key] = {
                    "title": item.title,
                    "link": item.link,
                    "published": item.published,
                }
            state["initialized"] = True
            state["last_title"] = result.title
            state["items"] = _prune_known_items(
                known, self._config.polling.max_known_items_per_feed
            )

        await self._store_feed_state(states)

    async def _fetch_feed(self, url: str) -> _FeedFetchResult:
        headers = {"User-Agent": self._config.polling.user_agent}
        async with httpx.AsyncClient(
            timeout=self._config.polling.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
        content = response.content
        if len(content) > self._config.polling.max_feed_bytes:
            raise ValueError(
                f"Feed response exceeds {self._config.polling.max_feed_bytes} bytes"
            )
        return _parse_feed(content, max_items=self._config.polling.max_items_per_feed)

    async def _notify(
        self,
        subscription: _FeedSubscription,
        item: _FeedItem,
        *,
        feed_title: str,
    ) -> None:
        text = _format_notification(
            feed_title=subscription.title or feed_title or subscription.url,
            item=item,
        )
        for target in subscription.targets:
            try:
                address = ChatAddress.parse(target)
                await self.api.send_message(
                    address.target_id,
                    OutboundMessage(
                        text=text,
                        extra={
                            "chat_address": address.chat_key,
                            "rss_feed_url": subscription.url,
                            "rss_item_key": item.key,
                        },
                    ),
                    channel=address.channel,
                )
                self.api.logger.info(
                    "rss_notifier.notify_sent",
                    feed_url=subscription.url,
                    item_key=item.key,
                    target=address.chat_key,
                )
            except Exception as exc:  # noqa: BLE001
                self.api.logger.exception(
                    "rss_notifier.notify_failed",
                    feed_url=subscription.url,
                    item_key=item.key,
                    target=target,
                    error=str(exc),
                )

    async def _subscription_map(self) -> dict[str, _FeedSubscription]:
        merged = self._configured_subscription_map()
        for key, sub in (await self._dynamic_subscription_map()).items():
            existing = merged.get(key)
            if existing is None:
                merged[key] = sub
                continue
            merged[key] = _FeedSubscription(
                key=key,
                url=existing.url,
                title=existing.title or sub.title,
                targets=tuple(_dedupe([*existing.targets, *sub.targets])),
                source="config+dynamic",
            )
        return merged

    def _configured_subscription_map(self) -> dict[str, _FeedSubscription]:
        subscriptions: dict[str, _FeedSubscription] = {}
        for feed in self._config.feeds:
            url = _normalize_url(feed.url)
            if not _is_supported_feed_url(url):
                self.api.logger.warning(
                    "rss_notifier.config_feed_ignored_invalid_url",
                    url=feed.url,
                )
                continue
            targets = feed.target_chat_addresses or self._config.target_chat_addresses
            key = _feed_key(url)
            subscriptions[key] = _FeedSubscription(
                key=key,
                url=url,
                title=feed.title,
                targets=tuple(_dedupe(targets)),
                source="config",
            )
        return subscriptions

    async def _dynamic_subscription_map(self) -> dict[str, _FeedSubscription]:
        data = await self._load_dynamic_data()
        raw_feeds = data.get("feeds", {})
        if not isinstance(raw_feeds, dict):
            return {}

        subscriptions: dict[str, _FeedSubscription] = {}
        for raw_key, raw in raw_feeds.items():
            if not isinstance(raw, dict):
                continue
            url = _normalize_url(str(raw.get("url", "")))
            if not _is_supported_feed_url(url):
                continue
            targets = [str(item) for item in raw.get("targets", [])]
            key = _feed_key(url)
            subscriptions[key] = _FeedSubscription(
                key=key,
                url=url,
                title=str(raw.get("title") or "").strip(),
                targets=tuple(_dedupe(targets)),
                source="dynamic",
            )
            if raw_key != key:
                self.api.logger.debug(
                    "rss_notifier.dynamic_feed_key_normalized",
                    old_key=str(raw_key),
                    new_key=key,
                )
        return subscriptions

    async def _subscriptions_for_target(self, target: str) -> list[_FeedSubscription]:
        subscriptions = await self._subscription_map()
        return sorted(
            [sub for sub in subscriptions.values() if target in sub.targets],
            key=lambda sub: (sub.title or sub.url).lower(),
        )

    async def _load_dynamic_data(self) -> dict[str, Any]:
        raw = await self.api.plugin_data_get(DYNAMIC_SUBSCRIPTIONS_KEY)
        if isinstance(raw, dict):
            feeds = raw.get("feeds")
            if isinstance(feeds, dict):
                return raw
        return {"feeds": {}}

    async def _store_dynamic_data(self, data: dict[str, Any]) -> None:
        await self.api.plugin_data_set(DYNAMIC_SUBSCRIPTIONS_KEY, data)

    async def _load_feed_state(self) -> dict[str, Any]:
        raw = await self.api.plugin_data_get(FEED_STATE_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    async def _store_feed_state(self, state: dict[str, Any]) -> None:
        await self.api.plugin_data_set(FEED_STATE_KEY, state)


def _parse_feed(content: bytes, *, max_items: int) -> _FeedFetchResult:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid feed XML: {exc}") from exc

    root_name = _local_name(root.tag).lower()
    if root_name == "feed":
        return _parse_atom(root, max_items=max_items)
    return _parse_rss(root, max_items=max_items)


def _parse_atom(root: ElementTree.Element, *, max_items: int) -> _FeedFetchResult:
    feed_title = _child_text(root, "title")
    items: list[_FeedItem] = []
    for entry in _children(root, "entry")[:max_items]:
        title = _clean_text(_child_text(entry, "title")) or "(untitled)"
        link = _atom_link(entry)
        published = _child_text(entry, "published") or _child_text(entry, "updated")
        summary = _clean_text(
            _child_text(entry, "summary") or _child_text(entry, "content")
        )
        key = (
            _child_text(entry, "id")
            or link
            or f"{title}|{published}"
            or hashlib.sha256(ElementTree.tostring(entry)).hexdigest()
        )
        items.append(
            _FeedItem(
                key=_stable_item_key(key),
                title=title,
                link=link,
                published=published,
                summary=summary,
            )
        )
    return _FeedFetchResult(title=_clean_text(feed_title), items=tuple(items))


def _parse_rss(root: ElementTree.Element, *, max_items: int) -> _FeedFetchResult:
    channel = _first_child(root, "channel") or root
    feed_title = _child_text(channel, "title")
    item_elements = _children(channel, "item")
    if not item_elements:
        item_elements = _children(root, "item")

    items: list[_FeedItem] = []
    for item in item_elements[:max_items]:
        title = _clean_text(_child_text(item, "title")) or "(untitled)"
        link = _child_text(item, "link")
        published = (
            _child_text(item, "pubDate")
            or _child_text(item, "published")
            or _child_text(item, "date")
        )
        summary = _clean_text(
            _child_text(item, "description")
            or _child_text(item, "summary")
            or _child_text(item, "encoded")
        )
        key = (
            _child_text(item, "guid")
            or _child_text(item, "id")
            or link
            or f"{title}|{published}"
            or hashlib.sha256(ElementTree.tostring(item)).hexdigest()
        )
        items.append(
            _FeedItem(
                key=_stable_item_key(key),
                title=title,
                link=link.strip(),
                published=published.strip(),
                summary=summary,
            )
        )
    return _FeedFetchResult(title=_clean_text(feed_title), items=tuple(items))


def _first_child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in list(element):
        if _local_name(child.tag).lower() == name.lower():
            return child
    return None


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [
        child
        for child in list(element)
        if _local_name(child.tag).lower() == name.lower()
    ]


def _child_text(element: ElementTree.Element, name: str) -> str:
    child = _first_child(element, name)
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def _atom_link(entry: ElementTree.Element) -> str:
    fallback = ""
    for child in _children(entry, "link"):
        href = (child.attrib.get("href") or "").strip()
        if not href:
            continue
        if not fallback:
            fallback = href
        rel = (child.attrib.get("rel") or "alternate").strip().lower()
        if rel == "alternate":
            return href
    return fallback


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag.rsplit(":", 1)[-1]


def _format_notification(*, feed_title: str, item: _FeedItem) -> str:
    lines = [f"📰 {feed_title}", f"📌 {item.title}"]
    if item.published:
        lines.append(f"🕒 {item.published}")
    if item.link:
        lines.append(f"🔗 {item.link}")
    if item.summary:
        lines.append("")
        lines.append(_truncate(item.summary, 300))
    return "\n".join(lines)


def _address_from_inbound(inbound: InboundMessage) -> ChatAddress:
    chat_type = ""
    if inbound.chat_context and inbound.chat_context.chat_type:
        chat_type = inbound.chat_context.chat_type
    elif inbound.message_context and inbound.message_context.chat_type:
        chat_type = inbound.message_context.chat_type
    if chat_type in {"private", "group", "channel", "thread"}:
        return ChatAddress(
            channel=inbound.platform,
            target_type=chat_type,  # type: ignore[arg-type]
            target_id=inbound.chat_id,
        )
    return ChatAddress(
        channel=inbound.platform,
        target_type="group" if inbound.is_group else "private",
        target_id=inbound.chat_id,
    )


def _normalize_url(value: str) -> str:
    return value.strip()


def _is_supported_feed_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _feed_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _stable_item_key(value: str) -> str:
    value = value.strip()
    if len(value) <= 180:
        return value
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _prune_known_items(known: dict[str, Any], limit: int) -> dict[str, Any]:
    items = list(known.items())
    if len(items) <= limit:
        return dict(items)
    return dict(items[-limit:])
