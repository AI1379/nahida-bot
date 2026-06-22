"""RSS/Atom notifier plugin for nahida-bot."""

from __future__ import annotations

import hashlib
import html
import re
import shlex
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from nahida_bot_sdk import (
    Attachment,
    ChatAddress,
    CommandResult,
    InboundMessage,
    OutboundMessage,
)
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


class RSSRenderingConfig(BaseModel):
    """Notification rendering behavior."""

    model_config = ConfigDict(extra="allow")

    mode: str = "standard"
    max_text_chars: int = Field(default=500, ge=0)
    max_paragraphs: int = Field(default=3, ge=0)
    include_images: bool = True
    max_images: int = Field(default=1, ge=0)
    send_image_attachments: bool = True
    image_download_timeout_seconds: float = Field(default=20.0, ge=1.0)
    max_image_bytes: int = Field(default=5_000_000, ge=1024)

    @field_validator("mode")
    @classmethod
    def _normalize_mode(cls, value: str) -> str:
        value = value.strip().lower()
        return value if value in {"compact", "standard", "rich"} else "standard"


class RSSNotifierConfig(BaseModel):
    """Plugin configuration."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    target_chat_addresses: list[str] = Field(default_factory=list)
    feeds: list[RSSFeedConfig] = Field(default_factory=list)
    polling: RSSPollingConfig = Field(default_factory=RSSPollingConfig)
    registration: RSSRegistrationConfig = Field(default_factory=RSSRegistrationConfig)
    rendering: RSSRenderingConfig = Field(default_factory=RSSRenderingConfig)

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
    paragraphs: tuple[str, ...] = ()
    image_urls: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class _FeedFetchResult:
    title: str
    items: tuple[_FeedItem, ...]


@dataclass(slots=True, frozen=True)
class _LatestEntry:
    feed_title: str
    item: _FeedItem
    sequence: int


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
            self.api.register_command(
                "rss_latest",
                self._cmd_latest,
                description=(
                    "Show latest RSS/Atom items for the current chat. "
                    "Usage: /rss_latest [n] [url-or-list-index-or-display-name]"
                ),
                aliases=["rss_recent"],
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
            self.api.logger.info(
                "rss_notifier.polling_not_started",
                enabled=self._config.enabled,
                polling_enabled=self._config.polling.enabled,
            )
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
        self.api.logger.info("rss_notifier.polling_stopped")

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
        self.api.logger.info(
            "rss_notifier.subscription_added",
            feed_url=url,
            feed_title=label,
            target=target,
            target_count=len(record["targets"]),
        )
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

        self.api.logger.info(
            "rss_notifier.subscription_removed",
            feed_url=selected.url,
            feed_title=selected.title,
            target=target,
            remaining_target_count=len(record.get("targets", [])),
        )
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
        del args, session_id
        target = _address_from_inbound(inbound).chat_key
        self.api.logger.info("rss_notifier.poll_command_requested", target=target)
        await self._poll_once_safely()
        return CommandResult.text("RSS polling pass completed.")

    async def _cmd_latest(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> CommandResult:
        del session_id
        try:
            limit, selector = _parse_latest_args(args)
        except ValueError as exc:
            return CommandResult.text(str(exc))

        target = _address_from_inbound(inbound).chat_key
        subscriptions = await self._subscriptions_for_target(target)
        if not subscriptions:
            return CommandResult.text("This chat is not subscribed to any RSS feeds.")

        selected = _select_latest_subscriptions(subscriptions, selector)
        if selected is None:
            self.api.logger.info(
                "rss_notifier.latest_no_matching_subscription",
                target=target,
                selector=selector,
                subscription_count=len(subscriptions),
            )
            return CommandResult.text("No matching RSS subscription found.")

        self.api.logger.info(
            "rss_notifier.latest_requested",
            target=target,
            limit=limit,
            selector=selector,
            subscription_count=len(subscriptions),
            selected_count=len(selected),
        )
        entries: list[_LatestEntry] = []
        failures: list[str] = []
        sequence = 0
        for sub in selected:
            try:
                result = await self._fetch_feed(sub.url)
            except Exception as exc:  # noqa: BLE001
                self.api.logger.exception(
                    "rss_notifier.latest_feed_fetch_failed",
                    feed_url=sub.url,
                    feed_title=sub.title,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                failures.append(f"{sub.title or sub.url}: {exc}")
                continue
            feed_title = sub.title or result.title or sub.url
            for item in result.items:
                entries.append(
                    _LatestEntry(
                        feed_title=feed_title,
                        item=item,
                        sequence=sequence,
                    )
                )
                sequence += 1

        if not entries and failures:
            return CommandResult.text(
                "Failed to fetch RSS subscriptions:\n"
                + "\n".join(f"- {failure}" for failure in failures[:5])
            )
        if not entries:
            return CommandResult.text("No RSS items found.")

        entries = sorted(entries, key=_latest_entry_sort_key, reverse=True)[:limit]
        attachments: list[Attachment] = []
        attached_image_item_keys: set[str] = set()
        fallback_image_urls_by_item: dict[str, list[str]] = {}
        if (
            self._config.rendering.send_image_attachments
            and self._config.rendering.mode != "compact"
        ):
            for entry in entries:
                if len(attachments) >= self._config.rendering.max_images:
                    break
                image_urls = list(self._rendered_image_urls(entry.item))
                if not image_urls:
                    continue
                item_fallbacks = fallback_image_urls_by_item.setdefault(
                    entry.item.key, []
                )
                for image_url in image_urls:
                    if len(attachments) >= self._config.rendering.max_images:
                        item_fallbacks.append(image_url)
                        continue
                    try:
                        attachments.append(
                            await self._download_image_attachment(
                                image_url,
                                feed_url=entry.item.link,
                                item_key=entry.item.key,
                                cleanup_after_send=True,
                            )
                        )
                        attached_image_item_keys.add(entry.item.key)
                    except Exception as exc:  # noqa: BLE001
                        item_fallbacks.append(image_url)
                        self.api.logger.warning(
                            "rss_notifier.latest_image_attachment_download_failed",
                            target=target,
                            image_url=image_url,
                            item_key=entry.item.key,
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
        text = _format_latest_entries(
            target=target,
            entries=entries,
            failures=failures,
            limit=limit,
            fallback_image_urls_by_item=fallback_image_urls_by_item,
            attached_image_item_keys=attached_image_item_keys,
        )
        self.api.logger.info(
            "rss_notifier.latest_completed",
            target=target,
            returned_count=len(entries),
            failure_count=len(failures),
            attachment_count=len(attachments),
        )
        return CommandResult.outbound(
            OutboundMessage(
                text=text,
                attachments=attachments,
                extra={
                    "rss_latest": True,
                    "rss_target": target,
                    "rss_selector": selector,
                },
            )
        )

    async def _poll_once_safely(self) -> None:
        try:
            await self._poll_once()
        except Exception as exc:  # noqa: BLE001
            self.api.logger.exception(
                "rss_notifier.poll_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

    async def _poll_once(self) -> None:
        started_at = perf_counter()
        subscriptions = await self._subscription_map()
        if not subscriptions:
            self.api.logger.debug("rss_notifier.poll_skipped_no_subscriptions")
            return

        active_subscriptions = [sub for sub in subscriptions.values() if sub.targets]
        if not active_subscriptions:
            self.api.logger.debug(
                "rss_notifier.poll_skipped_no_targets",
                subscription_count=len(subscriptions),
            )
            return

        self.api.logger.info(
            "rss_notifier.poll_started",
            subscription_count=len(subscriptions),
            active_subscription_count=len(active_subscriptions),
        )
        states = await self._load_feed_state()
        fetched_count = 0
        failed_count = 0
        baselined_count = 0
        new_item_count = 0
        notification_item_count = 0
        for sub in subscriptions.values():
            if not sub.targets:
                self.api.logger.debug(
                    "rss_notifier.poll_feed_skipped_no_targets",
                    feed_url=sub.url,
                    feed_title=sub.title,
                    source=sub.source,
                )
                continue
            feed_started_at = perf_counter()
            self.api.logger.debug(
                "rss_notifier.poll_feed_started",
                feed_url=sub.url,
                feed_title=sub.title,
                source=sub.source,
                target_count=len(sub.targets),
            )
            try:
                result = await self._fetch_feed(sub.url)
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                self.api.logger.exception(
                    "rss_notifier.feed_fetch_failed",
                    feed_url=sub.url,
                    feed_title=sub.title,
                    source=sub.source,
                    target_count=len(sub.targets),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue
            fetched_count += 1

            state = states.get(sub.key)
            if not isinstance(state, dict):
                state = {}
                states[sub.key] = state
            known = state.setdefault("items", {})
            if not isinstance(known, dict):
                known = {}
                state["items"] = known
            initialized = bool(state.get("initialized"))
            known_item_count = len(known)

            new_items = [item for item in result.items if item.key not in known]
            self.api.logger.info(
                "rss_notifier.poll_feed_checked",
                feed_url=sub.url,
                feed_title=sub.title or result.title,
                fetched_feed_title=result.title,
                source=sub.source,
                target_count=len(sub.targets),
                item_count=len(result.items),
                known_item_count=known_item_count,
                new_item_count=len(new_items),
                initialized=initialized,
                elapsed_ms=_elapsed_ms(feed_started_at),
            )
            if not initialized:
                baselined_count += 1
                self.api.logger.info(
                    "rss_notifier.feed_baselined",
                    feed_url=sub.url,
                    feed_title=sub.title or result.title,
                    item_count=len(result.items),
                    target_count=len(sub.targets),
                )
            if initialized and new_items:
                new_item_count += len(new_items)
                limit = self._config.polling.max_new_items_per_feed_per_poll
                notify_items = new_items[:limit]
                self.api.logger.info(
                    "rss_notifier.feed_new_items_detected",
                    feed_url=sub.url,
                    feed_title=sub.title or result.title,
                    new_item_count=len(new_items),
                    notification_item_count=len(notify_items),
                    suppressed_item_count=max(0, len(new_items) - len(notify_items)),
                    target_count=len(sub.targets),
                )
                for item in reversed(notify_items):
                    notification_item_count += 1
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
        self.api.logger.info(
            "rss_notifier.poll_completed",
            subscription_count=len(subscriptions),
            active_subscription_count=len(active_subscriptions),
            fetched_count=fetched_count,
            failed_count=failed_count,
            baselined_count=baselined_count,
            new_item_count=new_item_count,
            notification_item_count=notification_item_count,
            elapsed_ms=_elapsed_ms(started_at),
        )

    async def _fetch_feed(self, url: str) -> _FeedFetchResult:
        started_at = perf_counter()
        headers = {"User-Agent": self._config.polling.user_agent}
        async with httpx.AsyncClient(
            timeout=self._config.polling.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                response = exc.response
                self.api.logger.warning(
                    "rss_notifier.feed_http_error",
                    feed_url=url,
                    status_code=response.status_code,
                    reason=response.reason_phrase,
                    content_type=response.headers.get("content-type", ""),
                    response_bytes=len(response.content),
                    elapsed_ms=_elapsed_ms(started_at),
                    error=str(exc),
                )
                raise
            except httpx.HTTPError as exc:
                self.api.logger.warning(
                    "rss_notifier.feed_http_request_failed",
                    feed_url=url,
                    error_type=type(exc).__name__,
                    elapsed_ms=_elapsed_ms(started_at),
                    error=str(exc),
                )
                raise
        content = response.content
        if len(content) > self._config.polling.max_feed_bytes:
            self.api.logger.warning(
                "rss_notifier.feed_response_too_large",
                feed_url=url,
                response_bytes=len(content),
                max_feed_bytes=self._config.polling.max_feed_bytes,
                content_type=response.headers.get("content-type", ""),
                status_code=response.status_code,
                elapsed_ms=_elapsed_ms(started_at),
            )
            raise ValueError(
                f"Feed response exceeds {self._config.polling.max_feed_bytes} bytes"
            )
        try:
            result = _parse_feed(
                content, max_items=self._config.polling.max_items_per_feed
            )
        except ValueError as exc:
            self.api.logger.warning(
                "rss_notifier.feed_parse_failed",
                feed_url=url,
                response_bytes=len(content),
                content_type=response.headers.get("content-type", ""),
                status_code=response.status_code,
                elapsed_ms=_elapsed_ms(started_at),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self.api.logger.debug(
            "rss_notifier.feed_fetched",
            feed_url=url,
            feed_title=result.title,
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            response_bytes=len(content),
            item_count=len(result.items),
            elapsed_ms=_elapsed_ms(started_at),
        )
        return result

    async def _notify(
        self,
        subscription: _FeedSubscription,
        item: _FeedItem,
        *,
        feed_title: str,
    ) -> None:
        attachments: list[Attachment] = []
        fallback_image_urls: list[str] = []
        feed_label = subscription.title or feed_title or subscription.url
        self.api.logger.debug(
            "rss_notifier.notify_preparing",
            feed_url=subscription.url,
            feed_title=feed_label,
            item_key=item.key,
            item_title=item.title,
            target_count=len(subscription.targets),
            image_url_count=len(item.image_urls),
        )
        try:
            attachments, fallback_image_urls = await self._prepare_image_attachments(
                item,
                feed_url=subscription.url,
            )
        except Exception as exc:  # noqa: BLE001
            self.api.logger.exception(
                "rss_notifier.image_attachment_prepare_failed",
                feed_url=subscription.url,
                feed_title=feed_label,
                item_key=item.key,
                item_title=item.title,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            fallback_image_urls = list(self._rendered_image_urls(item))
        text = _format_notification(
            feed_title=feed_label,
            item=item,
            rendering=self._config.rendering,
            fallback_image_urls=tuple(fallback_image_urls),
        )
        try:
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
                                "rss_image_urls": list(item.image_urls),
                            },
                            attachments=attachments,
                        ),
                        channel=address.channel,
                    )
                    self.api.logger.info(
                        "rss_notifier.notify_sent",
                        feed_url=subscription.url,
                        feed_title=feed_label,
                        item_key=item.key,
                        item_title=item.title,
                        target=address.chat_key,
                        channel=address.channel,
                        attachment_count=len(attachments),
                        fallback_image_url_count=len(fallback_image_urls),
                    )
                except Exception as exc:  # noqa: BLE001
                    self.api.logger.exception(
                        "rss_notifier.notify_failed",
                        feed_url=subscription.url,
                        feed_title=feed_label,
                        item_key=item.key,
                        item_title=item.title,
                        target=target,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
        finally:
            for attachment in attachments:
                try:
                    await self.api.cleanup_temp_attachment(attachment)
                except Exception as exc:  # noqa: BLE001
                    self.api.logger.warning(
                        "rss_notifier.image_attachment_cleanup_failed",
                        feed_url=subscription.url,
                        item_key=item.key,
                        path=attachment.path,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )

    async def _prepare_image_attachments(
        self, item: _FeedItem, *, feed_url: str
    ) -> tuple[list[Attachment], list[str]]:
        image_urls = list(self._rendered_image_urls(item))
        if (
            not image_urls
            or not self._config.rendering.send_image_attachments
            or self._config.rendering.mode == "compact"
        ):
            if image_urls:
                self.api.logger.debug(
                    "rss_notifier.image_attachment_skipped",
                    feed_url=feed_url,
                    item_key=item.key,
                    image_url_count=len(image_urls),
                    send_image_attachments=(
                        self._config.rendering.send_image_attachments
                    ),
                    rendering_mode=self._config.rendering.mode,
                )
            return [], image_urls

        attachments: list[Attachment] = []
        fallback_urls: list[str] = []
        for image_url in image_urls:
            try:
                attachments.append(
                    await self._download_image_attachment(
                        image_url,
                        feed_url=feed_url,
                        item_key=item.key,
                        cleanup_after_send=False,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                fallback_urls.append(image_url)
                self.api.logger.warning(
                    "rss_notifier.image_attachment_download_failed",
                    feed_url=feed_url,
                    image_url=image_url,
                    item_key=item.key,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        return attachments, fallback_urls

    def _rendered_image_urls(self, item: _FeedItem) -> tuple[str, ...]:
        rendering = self._config.rendering
        if not rendering.include_images or rendering.max_images <= 0:
            return ()
        return tuple(_dedupe(list(item.image_urls))[: rendering.max_images])

    async def _download_image_attachment(
        self,
        image_url: str,
        *,
        feed_url: str,
        item_key: str,
        cleanup_after_send: bool = True,
    ) -> Attachment:
        if not _is_supported_feed_url(image_url):
            raise ValueError("image URL must be http(s)")
        started_at = perf_counter()
        rendering = self._config.rendering
        headers = {"User-Agent": self._config.polling.user_agent}
        async with httpx.AsyncClient(
            timeout=rendering.image_download_timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            try:
                stream_cm = client.stream("GET", image_url)
                async with stream_cm as response:
                    response.raise_for_status()
                    attachment = await self._read_image_attachment(
                        response=response,
                        image_url=image_url,
                        max_image_bytes=rendering.max_image_bytes,
                        content_type=response.headers.get("content-type", ""),
                        cleanup_after_send=cleanup_after_send,
                    )
            except httpx.HTTPStatusError as exc:
                response = exc.response
                self.api.logger.warning(
                    "rss_notifier.image_http_error",
                    feed_url=feed_url,
                    item_key=item_key,
                    image_url=image_url,
                    status_code=response.status_code,
                    reason=response.reason_phrase,
                    content_type=response.headers.get("content-type", ""),
                    elapsed_ms=_elapsed_ms(started_at),
                    error=str(exc),
                )
                raise
            except httpx.HTTPError as exc:
                self.api.logger.warning(
                    "rss_notifier.image_http_request_failed",
                    feed_url=feed_url,
                    item_key=item_key,
                    image_url=image_url,
                    error_type=type(exc).__name__,
                    elapsed_ms=_elapsed_ms(started_at),
                    error=str(exc),
                )
                raise
        self.api.logger.debug(
            "rss_notifier.image_attachment_downloaded",
            feed_url=feed_url,
            item_key=item_key,
            image_url=image_url,
            path=attachment.path,
            mime_type=attachment.mime_type,
            elapsed_ms=_elapsed_ms(started_at),
        )
        return attachment

    async def _read_image_attachment(
        self,
        *,
        response: httpx.Response,
        image_url: str,
        max_image_bytes: int,
        content_type: str,
        cleanup_after_send: bool,
    ) -> Attachment:
        content_type = _normalize_content_type(content_type)
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"image URL returned {content_type!r}")
        content_length = _parse_int_header(response.headers.get("content-length", ""))
        if content_length is not None and content_length > max_image_bytes:
            raise ValueError(f"image exceeds {max_image_bytes} bytes")

        suffix = _image_suffix(image_url, content_type)
        temp_file = await self.api.create_temp_file(
            suffix=suffix,
            prefix="rss-image",
            purpose="rss-image-attachment",
            ttl_seconds=3600,
        )
        path = Path(temp_file.path)
        total = 0
        try:
            with path.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_image_bytes:
                        raise ValueError(f"image exceeds {max_image_bytes} bytes")
                    file.write(chunk)
        except Exception:
            _unlink_quietly(path)
            raise

        if total <= 0:
            _unlink_quietly(path)
            raise ValueError("image response was empty")

        return temp_file.as_attachment(
            type="photo",
            filename=path.name,
            mime_type=content_type or "image/*",
            cleanup_after_send=cleanup_after_send,
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
        content_fragment = _child_markup(entry, "summary") or _child_markup(
            entry, "content"
        )
        content = _parse_html_content(content_fragment, base_url=link)
        paragraphs = content.paragraphs
        summary = _summary_from_paragraphs(paragraphs)
        image_urls = _dedupe(
            [
                *_media_image_urls(entry, base_url=link),
                *content.image_urls,
            ]
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
                paragraphs=tuple(paragraphs),
                image_urls=tuple(image_urls),
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
        content_fragment = (
            _child_markup(item, "description")
            or _child_markup(item, "summary")
            or _child_markup(item, "encoded")
        )
        content = _parse_html_content(content_fragment, base_url=link)
        paragraphs = content.paragraphs
        summary = _summary_from_paragraphs(paragraphs)
        image_urls = _dedupe(
            [
                *_media_image_urls(item, base_url=link),
                *content.image_urls,
            ]
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
                paragraphs=tuple(paragraphs),
                image_urls=tuple(image_urls),
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


def _child_markup(element: ElementTree.Element, name: str) -> str:
    child = _first_child(element, name)
    if child is None:
        return ""
    parts: list[str] = []
    if child.text:
        parts.append(child.text)
    for sub in list(child):
        parts.append(ElementTree.tostring(sub, encoding="unicode"))
        if sub.tail:
            parts.append(sub.tail)
    return "".join(parts).strip()


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


@dataclass(slots=True, frozen=True)
class _ParsedHTMLContent:
    paragraphs: tuple[str, ...] = ()
    image_urls: tuple[str, ...] = ()


class _HTMLContentParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "div",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "pre",
        "section",
        "td",
        "th",
        "tr",
    }
    _IGNORED_TAGS = {"script", "style", "iframe", "object", "noscript"}

    def __init__(self, *, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._parts: list[str] = []
        self.paragraphs: list[str] = []
        self.image_urls: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in self._BLOCK_TAGS:
            self._flush()
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "img":
            attrs_dict = {key.lower(): value or "" for key, value in attrs}
            src = attrs_dict.get("src", "").strip()
            if src:
                self.image_urls.append(_absolute_url(src, self._base_url))
            alt = attrs_dict.get("alt", "").strip()
            if alt:
                self._parts.append(f" {alt} ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if data:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        paragraph = _clean_paragraph("".join(self._parts))
        self._parts.clear()
        if paragraph:
            self.paragraphs.append(paragraph)


def _parse_html_content(fragment: str, *, base_url: str = "") -> _ParsedHTMLContent:
    fragment = html.unescape(fragment or "")
    if not fragment.strip():
        return _ParsedHTMLContent()
    parser = _HTMLContentParser(base_url=base_url)
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        fallback = _clean_text(fragment)
        return _ParsedHTMLContent(paragraphs=(fallback,) if fallback else ())
    return _ParsedHTMLContent(
        paragraphs=tuple(parser.paragraphs),
        image_urls=tuple(_dedupe(parser.image_urls)),
    )


def _media_image_urls(element: ElementTree.Element, *, base_url: str = "") -> list[str]:
    urls: list[str] = []
    for child in list(element):
        local = _local_name(child.tag).lower()
        attrs = {key.lower(): value for key, value in child.attrib.items()}
        if local == "enclosure":
            raw_url = attrs.get("url", "").strip()
            raw_type = attrs.get("type", "").strip().lower()
            if raw_url and (
                raw_type.startswith("image/") or _looks_like_image_url(raw_url)
            ):
                urls.append(_absolute_url(raw_url, base_url))
        elif local in {"thumbnail", "content"}:
            raw_url = (attrs.get("url") or attrs.get("src") or "").strip()
            raw_type = attrs.get("type", "").strip().lower()
            medium = attrs.get("medium", "").strip().lower()
            if raw_url and (
                local == "thumbnail"
                or medium == "image"
                or raw_type.startswith("image/")
                or _looks_like_image_url(raw_url)
            ):
                urls.append(_absolute_url(raw_url, base_url))
        elif local == "link":
            raw_url = attrs.get("href", "").strip()
            raw_type = attrs.get("type", "").strip().lower()
            rel = attrs.get("rel", "").strip().lower()
            if raw_url and rel == "enclosure" and raw_type.startswith("image/"):
                urls.append(_absolute_url(raw_url, base_url))
    return _dedupe(urls)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag.rsplit(":", 1)[-1]


def _format_notification(
    *,
    feed_title: str,
    item: _FeedItem,
    rendering: RSSRenderingConfig | None = None,
    fallback_image_urls: tuple[str, ...] = (),
) -> str:
    rendering = rendering or RSSRenderingConfig()
    lines = [f"📰 {feed_title}", f"📌 {item.title}"]
    if item.published:
        lines.append(f"🕒 {item.published}")
    if item.link:
        lines.append(f"🔗 {item.link}")
    raw_paragraphs = item.paragraphs or ((item.summary,) if item.summary else ())
    paragraphs = _select_paragraphs(raw_paragraphs, rendering)
    if paragraphs:
        lines.append("")
        lines.append("\n\n".join(paragraphs))
    if fallback_image_urls and rendering.include_images and rendering.mode != "compact":
        lines.append("")
        for image_url in fallback_image_urls[: rendering.max_images]:
            lines.append(f"🖼 {image_url}")
    return "\n".join(lines)


def _parse_latest_args(args: str) -> tuple[int, str]:
    try:
        parts = shlex.split(args)
    except ValueError as exc:
        raise ValueError(f"Invalid arguments: {exc}") from exc

    limit = 5
    selector_parts = parts
    if parts:
        try:
            parsed_limit = int(parts[0])
        except ValueError:
            parsed_limit = 0
        if parsed_limit > 0:
            limit = min(parsed_limit, 20)
            selector_parts = parts[1:]
    selector = " ".join(selector_parts).strip()
    return limit, selector


def _select_latest_subscriptions(
    subscriptions: list[_FeedSubscription], selector: str
) -> list[_FeedSubscription] | None:
    if not selector:
        return subscriptions
    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(subscriptions):
            return [subscriptions[index - 1]]
        return None
    normalized = _normalize_url(selector)
    selected = [sub for sub in subscriptions if sub.url == normalized]
    if selected:
        return selected

    selector_key = selector.casefold()
    selected = [
        sub
        for sub in subscriptions
        if sub.title and sub.title.casefold() == selector_key
    ]
    if selected:
        return selected

    selected = [
        sub
        for sub in subscriptions
        if sub.title and selector_key in sub.title.casefold()
    ]
    return selected or None


def _latest_entry_sort_key(entry: _LatestEntry) -> tuple[int, int]:
    timestamp = _published_timestamp(entry.item.published)
    if timestamp is None:
        return (0, -entry.sequence)
    return (1, int(timestamp))


def _published_timestamp(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        iso_value = value
        if iso_value.endswith("Z"):
            iso_value = iso_value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(iso_value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _format_latest_entries(
    *,
    target: str,
    entries: list[_LatestEntry],
    failures: list[str],
    limit: int,
    fallback_image_urls_by_item: dict[str, list[str]] | None = None,
    attached_image_item_keys: set[str] | None = None,
) -> str:
    fallback_image_urls_by_item = fallback_image_urls_by_item or {}
    attached_image_item_keys = attached_image_item_keys or set()
    lines = [f"Latest {len(entries)} RSS item(s) for {target}:"]
    for index, entry in enumerate(entries, start=1):
        item = entry.item
        lines.append(f"{index}. [{entry.feed_title}] {item.title}")
        if item.published:
            lines.append(f"   🕒 {item.published}")
        if item.link:
            lines.append(f"   🔗 {item.link}")
        if item.paragraphs:
            preview = _truncate(" ".join(item.paragraphs), 160)
            if preview:
                lines.append(f"   {preview}")
        image_url = next(iter(fallback_image_urls_by_item.get(item.key, ())), "")
        if not image_url and item.image_urls:
            if item.key not in attached_image_item_keys:
                image_url = item.image_urls[0]
        if image_url:
            lines.append(f"   🖼 {image_url}")
    if failures:
        lines.append("")
        lines.append("Fetch failures:")
        for failure in failures[: max(1, min(3, limit))]:
            lines.append(f"- {failure}")
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


def _clean_paragraph(value: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _summary_from_paragraphs(paragraphs: tuple[str, ...]) -> str:
    return " ".join(paragraphs).strip()


def _select_paragraphs(
    paragraphs: tuple[str, ...], rendering: RSSRenderingConfig
) -> list[str]:
    if rendering.mode == "compact":
        max_paragraphs = 1
        max_chars = (
            min(rendering.max_text_chars, 240) if rendering.max_text_chars else 0
        )
    else:
        max_paragraphs = rendering.max_paragraphs
        max_chars = rendering.max_text_chars
    if max_paragraphs <= 0 or max_chars <= 0:
        return []

    selected: list[str] = []
    used = 0
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        remaining = max_chars - used
        if remaining <= 0 or len(selected) >= max_paragraphs:
            break
        if len(paragraph) > remaining:
            selected.append(_truncate(paragraph, remaining))
            break
        selected.append(paragraph)
        used += len(paragraph)
    return selected


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _absolute_url(value: str, base_url: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("//"):
        parsed_base = urlparse(base_url)
        scheme = (
            parsed_base.scheme if parsed_base.scheme in {"http", "https"} else "https"
        )
        return f"{scheme}:{value}"
    if base_url:
        return urljoin(base_url, value)
    return value


def _looks_like_image_url(value: str) -> bool:
    path = urlparse(value).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"))


def _normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _parse_int_header(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _image_suffix(image_url: str, content_type: str) -> str:
    by_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/avif": ".avif",
    }
    suffix = by_type.get(content_type)
    if suffix:
        return suffix
    path_suffix = Path(urlparse(image_url).path).suffix.lower()
    if path_suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}:
        return path_suffix
    return ".img"


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
