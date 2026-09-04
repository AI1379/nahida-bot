"""Feishu Channel plugin."""

from __future__ import annotations

import asyncio
import json
import time
import uuid as uuid_module
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.feishu._parsing import as_mapping, coerce_str
from nahida_bot.channels.feishu.client import FeishuClient, FeishuClientError
from nahida_bot.channels.feishu.config import FeishuPluginConfig, parse_feishu_config
from nahida_bot.channels.feishu.event_stream import FeishuEventStream
from nahida_bot.channels.feishu.message_converter import (
    RECEIVE_EVENT_TYPE,
    FeishuMessageConverter,
)
from nahida_bot.channels.feishu.segment_converter import (
    FeishuOutboundConverter,
    FeishuSendItem,
    FeishuTargetError,
    message_id_from_send_result,
    resolve_target,
)
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import MessageObserved, MessagePayload, MessageReceived
from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.core.outbound_mentions import extract_mention_ids, parse_outbound_parts
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import (
    InboundAttachment,
    MediaDownloadResult,
    OutboundMessage,
    Plugin,
)

if TYPE_CHECKING:
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)

_DEDUP_LRU_SIZE = 1024
_MEMBER_CACHE_MAX = 256
_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # platform limit for message resources


class _ChatMembers:
    """Cached member snapshot for one chat."""

    __slots__ = ("open_ids", "names", "expires_at")

    def __init__(
        self, open_ids: frozenset[str], names: dict[str, str], expires_at: float
    ) -> None:
        self.open_ids = open_ids
        self.names = names
        self.expires_at = expires_at


class FeishuPlugin(Plugin):
    """Feishu channel plugin (SDK WebSocket events + OpenAPI REST sends)."""

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id = manifest.id
        self._config = parse_feishu_config(manifest.config)
        self._client: FeishuClient | None = None
        self._event_stream: FeishuEventStream | None = None
        self._inbound_converter: FeishuMessageConverter | None = None
        self._outbound_converter: FeishuOutboundConverter | None = None
        self._self_open_id = ""
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._member_cache: OrderedDict[str, _ChatMembers] = OrderedDict()
        self._chat_name_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._bot_info_task: asyncio.Task[None] | None = None

    # ── ChannelService surface ────────────────────────────────────

    @property
    def channel_id(self) -> str:
        """Unique identifier used by the channel registry."""
        return self._channel_id

    @property
    def self_open_id(self) -> str:
        """Bot open_id reported by Feishu, if known."""
        return self._self_open_id

    @property
    def config(self) -> FeishuPluginConfig:
        """Parsed Feishu plugin configuration."""
        return self._config

    @property
    def reply_to_inbound(self) -> bool | None:
        """Optional channel override for router default reply-to behavior."""
        return self.config.reply_to_inbound

    # ── lifecycle ─────────────────────────────────────────────────

    async def on_load(self) -> None:
        """Create the API client, best-effort bot identity, register channel."""
        self._ensure_runtime_client()
        await self._refresh_bot_info_once(log_failure=True)
        logger.info(
            "feishu.loaded",
            domain=self.config.domain,
            group_trigger_mode=self.config.group_trigger_mode,
            group_context_capture=self.config.group_context_capture,
            markdown_enabled=self.config.markdown_enabled,
            channel=self.channel_id,
            self_open_id=self._self_open_id,
        )
        self.api.register_channel(self)
        self.api.register_prompt_supplement(
            key="markdown_rendering",
            instruction=(
                "The current channel renders basic Markdown as rich text: "
                "**bold**, *italic*, ~~strikethrough~~, `inline code`, "
                "[links](url), - bullet lists, 1. numbered lists, # headings, "
                "> quotes, and ```code blocks``` are all rendered. Tables are "
                "NOT rendered — present tabular data as lists instead."
            ),
            channel=self.channel_id,
        )

    async def on_enable(self) -> None:
        """Start the SDK WebSocket event stream and optional tools."""
        self._ensure_runtime_client()
        if not self._self_open_id:
            self._start_bot_info_retry()
        self._event_stream = FeishuEventStream(self.config, self.handle_inbound_event)
        self._event_stream.start()
        if self.config.enable_media_download_tool:
            self._register_file_download_tool()
        logger.info("feishu.event_stream_started", channel=self.channel_id)

    async def on_disable(self) -> None:
        """Stop the event stream and close HTTP client resources."""
        if self._event_stream is not None:
            await self._event_stream.stop()
            self._event_stream = None
        await self._stop_bot_info_retry()
        if self._client is not None:
            await self._client.close()
            self._client = None
        logger.info("feishu.stopped", channel=self.channel_id)

    # ── inbound ───────────────────────────────────────────────────

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Normalize one Feishu event and publish a bot event."""
        try:
            await self._handle_inbound_event_inner(event)
        except Exception as exc:  # noqa: BLE001 - bridge drops futures silently
            logger.exception(
                "feishu.inbound_event_failed",
                error=str(exc),
                channel=self.channel_id,
            )

    async def _handle_inbound_event_inner(self, event: dict[str, Any]) -> None:
        header = as_mapping(event.get("header"))
        event_type = coerce_str(header.get("event_type"))
        if event_type != RECEIVE_EVENT_TYPE:
            logger.debug(
                "feishu.event_ignored", event_type=event_type, channel=self.channel_id
            )
            return

        message = as_mapping(as_mapping(event.get("event")).get("message"))
        message_id = coerce_str(message.get("message_id"))
        sender_type = coerce_str(
            as_mapping(as_mapping(event.get("event")).get("sender")).get("sender_type")
        )
        if sender_type == "bot":
            # Loop prevention: never react to bot senders (covers the
            # include_bot scope delivering other bots' @-mentions).
            logger.debug("feishu.bot_sender_skipped", message_id=message_id)
            return
        if message_id:
            if message_id in self._seen_message_ids:
                logger.debug("feishu.duplicate_event_skipped", message_id=message_id)
                return
            self._seen_message_ids[message_id] = None
            self._seen_message_ids.move_to_end(message_id)
            while len(self._seen_message_ids) > _DEDUP_LRU_SIZE:
                self._seen_message_ids.popitem(last=False)

        converter = self._ensure_inbound_converter()
        inbound = await converter.to_inbound(event)
        if inbound is None:
            logger.debug(
                "feishu.message_filtered_by_converter",
                chat_id=coerce_str(message.get("chat_id")),
                message_id=message_id,
                channel=self.channel_id,
            )
            return
        logger.debug(
            "feishu.message_normalized",
            channel=self.channel_id,
            chat_id=inbound.chat_id,
            message_id=inbound.message_id,
            is_group=inbound.is_group,
            mentions_bot=inbound.mentions_bot,
            text_preview=inbound.text[:120],
        )

        inbound = await self._enrich_chat_display_name(inbound)

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        logger.debug(
            "feishu.message_decision",
            channel=self.channel_id,
            reason=decision.reason,
            observe=decision.observe,
            respond=decision.respond,
            group_trigger_mode=self.config.group_trigger_mode,
        )

        if not decision.observe:
            logger.debug(
                "feishu.message_filtered",
                reason=decision.reason,
                chat_id=inbound.chat_id,
                message_id=inbound.message_id,
                mentions_bot=inbound.mentions_bot,
            )
            return

        if (
            decision.respond
            and self.config.cache_media_on_receive
            and inbound.attachments
        ):
            inbound = await self._cache_inbound_attachments(inbound)

        await self._publish_message_event(
            inbound,
            event_cls=MessageReceived if decision.respond else MessageObserved,
            decision_reason=decision.reason,
        )

    async def _publish_message_event(
        self,
        inbound: Any,
        *,
        event_cls: type[MessageReceived] | type[MessageObserved],
        decision_reason: str = "",
    ) -> None:
        address = ChatAddress.from_inbound(
            inbound.platform,
            inbound.chat_id,
            chat_type="group" if inbound.is_group else "private",
        )
        if not address.is_typed:
            logger.warning("feishu.chat_type_missing", chat_id=inbound.chat_id)
            return
        session_id = MessageRouter.make_session_id(address)
        await self.api.publish_event(
            event_cls(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="feishu",
            )
        )
        logger.debug(
            "feishu.message_published",
            channel=self.channel_id,
            emitted_event=event_cls.__name__,
            session_id=session_id,
            decision_reason=decision_reason,
        )

    async def _enrich_chat_display_name(self, inbound: Any) -> Any:
        """Fill chat_context.display_name (group name) when resolvable."""
        if not inbound.is_group:
            return inbound
        chat_context = inbound.chat_context
        if chat_context is not None and chat_context.display_name:
            return inbound
        name = await self._chat_display_name(inbound.chat_id)
        if not name:
            return inbound
        new_chat = replace(chat_context, display_name=name)
        new_message_context = (
            replace(inbound.message_context, chat_display_name=name)
            if inbound.message_context is not None
            else inbound.message_context
        )
        return replace(
            inbound,
            chat_context=new_chat,
            message_context=new_message_context,
        )

    # ── outbound ──────────────────────────────────────────────────

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send one normalized outbound message to Feishu."""
        logger.debug(
            "feishu.send_start",
            channel=self.channel_id,
            target=target,
            text_chars=len(message.text),
            attachment_count=len(message.attachments),
        )
        client = self._ensure_client()
        converter = self._ensure_outbound_converter()

        if message.reasoning:
            thinking = f"[💭 思考过程]\n{message.reasoning}"
            combined = f"{thinking}\n\n{message.text}" if message.text else thinking
            message = OutboundMessage(
                text=combined,
                reply_to=message.reply_to,
                extra=message.extra,
                attachments=message.attachments,
            )

        try:
            receive_id_type, receive_id = resolve_target(target, message)
        except FeishuTargetError as exc:
            logger.warning(
                "feishu.target_invalid",
                target=target,
                error=str(exc),
                channel=self.channel_id,
            )
            return ""

        if self.config.outbound_mentions_enabled and message.text:
            message = await self._with_validated_mentions(client, receive_id, message)

        payload = converter.to_payload(message)
        if not payload.items:
            return ""

        send_uuid = uuid_module.uuid4().hex
        last_id = ""
        for index, item in enumerate(payload.items):
            try:
                message_id = await self._send_item(
                    client,
                    item,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    reply_to=payload.reply_to if index == 0 else "",
                    uuid=f"{send_uuid}-{index}",
                )
            except FeishuClientError as exc:
                fallback_id = await self._send_item_fallback(
                    client,
                    item,
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    reply_to=payload.reply_to if index == 0 else "",
                    uuid=f"{send_uuid}-{index}",
                    error=exc,
                )
                if not fallback_id:
                    raise
                message_id = fallback_id
            last_id = message_id or last_id

        logger.debug(
            "feishu.send_done",
            channel=self.channel_id,
            target=target,
            message_id=last_id,
            item_count=len(payload.items),
        )
        return last_id

    async def _send_item(
        self,
        client: FeishuClient,
        item: FeishuSendItem,
        *,
        receive_id_type: str,
        receive_id: str,
        reply_to: str,
        uuid: str,
    ) -> str:
        if item.kind in {"text", "post"}:
            msg_type = "text" if item.kind == "text" else "post"
            if reply_to:
                result = await client.reply_message(
                    message_id=reply_to,
                    msg_type=msg_type,
                    content=item.content,
                    uuid=uuid,
                )
            else:
                result = await client.send_message(
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    msg_type=msg_type,
                    content=item.content,
                    uuid=uuid,
                )
            return message_id_from_send_result(result)

        data = _read_file_bytes(item.path, limit=_MAX_DOWNLOAD_BYTES)
        if data is None:
            logger.warning("feishu.attachment_read_failed", path=item.path)
            return ""

        if item.kind == "image":
            image_key = await client.upload_image(data=data, file_name=item.file_name)
            if not image_key:
                return ""
            content = json.dumps({"image_key": image_key}, ensure_ascii=False)
            result = await client.send_message(
                receive_id_type=receive_id_type,
                receive_id=receive_id,
                msg_type="image",
                content=content,
                uuid=uuid,
            )
            return message_id_from_send_result(result)

        file_key = await client.upload_file(
            data=data,
            file_name=item.file_name,
            file_type=item.file_type,
            duration_ms=item.duration_ms,
        )
        if not file_key:
            return ""
        content = json.dumps({"file_key": file_key}, ensure_ascii=False)
        result = await client.send_message(
            receive_id_type=receive_id_type,
            receive_id=receive_id,
            msg_type="file",
            content=content,
            uuid=uuid,
        )
        return message_id_from_send_result(result)

    async def _send_item_fallback(
        self,
        client: FeishuClient,
        item: FeishuSendItem,
        *,
        receive_id_type: str,
        receive_id: str,
        reply_to: str,
        uuid: str,
        error: FeishuClientError,
    ) -> str:
        """Degrade one failed rich send to a safe simpler form."""
        if item.kind != "post":
            return ""
        # Post rejected (e.g. oversized) — resend the same chunk as plain text.
        plain = _post_content_to_plain_text(item.content)
        if not plain:
            return ""
        logger.warning(
            "feishu.post_send_failed_fallback_text",
            receive_id=receive_id,
            error=str(error),
            channel=self.channel_id,
        )
        try:
            if reply_to:
                result = await client.reply_message(
                    message_id=reply_to,
                    msg_type="text",
                    content=json.dumps({"text": plain}, ensure_ascii=False),
                    uuid=uuid,
                )
            else:
                result = await client.send_message(
                    receive_id_type=receive_id_type,
                    receive_id=receive_id,
                    msg_type="text",
                    content=json.dumps({"text": plain}, ensure_ascii=False),
                    uuid=uuid,
                )
        except FeishuClientError:
            return ""
        return message_id_from_send_result(result)

    # ── outbound mentions ─────────────────────────────────────────

    async def _with_validated_mentions(
        self,
        client: FeishuClient,
        chat_id: str,
        message: OutboundMessage,
    ) -> OutboundMessage:
        """Validate mention tokens against current chat membership.

        Passing ids land in ``extra["feishu_mention_ids"]`` for the outbound
        converter; every other token stays literal text so a hallucinated or
        stale id degrades instead of breaking the send. The member list API
        does not return bots, so @bot tokens always degrade — by design.
        """
        if not any(part.is_mention for part in parse_outbound_parts(message.text)):
            return message
        requested = extract_mention_ids(
            message.text, limit=self.config.max_mentions_per_message
        )
        members = await self._chat_members(client, chat_id)
        validated = [open_id for open_id in requested if open_id in members.open_ids]
        degraded = [
            part.user_id
            for part in parse_outbound_parts(message.text)
            if part.is_mention and part.user_id not in validated
        ]
        logger.info(
            "feishu.mention_outbound",
            channel=self.channel_id,
            chat_id=chat_id,
            requested_ids=requested,
            validated_ids=validated,
            degraded_ids=list(dict.fromkeys(degraded)),
        )
        if not validated or "feishu_mention_ids" in message.extra:
            return message
        extra = dict(message.extra)
        extra["feishu_mention_ids"] = validated
        return OutboundMessage(
            text=message.text,
            reply_to=message.reply_to,
            extra=extra,
            attachments=message.attachments,
        )

    async def _chat_members(self, client: FeishuClient, chat_id: str) -> _ChatMembers:
        """Return the chat member snapshot with TTL caching."""
        now = time.monotonic()
        cached = self._member_cache.get(chat_id)
        if cached is not None and now < cached.expires_at:
            self._member_cache.move_to_end(chat_id)
            return cached
        try:
            raw_members = await client.get_chat_members(chat_id)
        except FeishuClientError as exc:
            logger.warning(
                "feishu.chat_members_fetch_failed",
                chat_id=chat_id,
                error=str(exc),
                channel=self.channel_id,
            )
            # Negative cache briefly so bursts of mentions don't hammer a
            # failing endpoint; membership checks degrade to literal text.
            snapshot = _ChatMembers(frozenset(), {}, now + 60.0)
        else:
            names: dict[str, str] = {}
            open_ids: set[str] = set()
            for member in raw_members:
                open_id = coerce_str(member.get("member_id"))
                if not open_id:
                    continue
                open_ids.add(open_id)
                name = coerce_str(member.get("name"))
                if name:
                    names[open_id] = name
            snapshot = _ChatMembers(
                frozenset(open_ids), names, now + self.config.member_cache_seconds
            )
        self._member_cache[chat_id] = snapshot
        self._member_cache.move_to_end(chat_id)
        while len(self._member_cache) > _MEMBER_CACHE_MAX:
            self._member_cache.popitem(last=False)
        return snapshot

    async def _lookup_member_name(self, chat_id: str, open_id: str) -> str:
        """Resolve one member's display name via the member cache."""
        client = self._ensure_client()
        members = await self._chat_members(client, chat_id)
        return members.names.get(open_id, "")

    async def _chat_display_name(self, chat_id: str) -> str:
        """Resolve a group's display name with TTL caching."""
        now = time.monotonic()
        cached = self._chat_name_cache.get(chat_id)
        if cached is not None and now < cached[1]:
            self._chat_name_cache.move_to_end(chat_id)
            return cached[0]
        name = ""
        try:
            info = await self._ensure_client().get_chat_info(chat_id)
            name = coerce_str(info.get("name"))
        except FeishuClientError as exc:
            logger.debug(
                "feishu.chat_info_fetch_failed", chat_id=chat_id, error=str(exc)
            )
        self._chat_name_cache[chat_id] = (name, now + self.config.member_cache_seconds)
        self._chat_name_cache.move_to_end(chat_id)
        while len(self._chat_name_cache) > _MEMBER_CACHE_MAX:
            self._chat_name_cache.popitem(last=False)
        return name

    # ── media ─────────────────────────────────────────────────────

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        """Fetch group metadata for LLM tools."""
        try:
            return await self._ensure_client().get_chat_info(group_id)
        except FeishuClientError as exc:
            logger.warning(
                "feishu.get_group_info_failed", group_id=group_id, error=str(exc)
            )
            return {}

    async def download_media(
        self, file_id: str, *, file_name: str = ""
    ) -> MediaDownloadResult | None:
        """Download one message resource by ``platform_id`` (``message_id:file_key``)."""
        message_id, _, file_key = file_id.partition(":")
        if not message_id or not file_key:
            logger.warning("feishu.download_media_invalid_id", file_id=file_id)
            return None

        cache_key = f"feishu:{self.channel_id}:{message_id}:{file_key}"
        store = self._media_store()
        try:
            if store is not None:
                entry = await store.get_or_create(
                    cache_key, lambda: self._media_payload(message_id, file_key)
                )
                return MediaDownloadResult(
                    path=entry.path,
                    file_name=entry.file_name or file_name,
                    mime_type=entry.mime_type,
                    file_size=entry.file_size,
                )
            data = await self._ensure_client().download_resource(
                message_id=message_id, file_key=file_key, resource_type="file"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feishu.download_media_failed", file_id=file_id, error=str(exc)
            )
            return None

        directory = Path(self.config.media_download_dir)
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = file_name or f"{file_key[-16:] or 'feishu'}.bin"
        path = directory / f"{message_id[-12:]}-{safe_name}"
        path.write_bytes(data)
        return MediaDownloadResult(
            path=str(path),
            file_name=safe_name,
            file_size=len(data),
        )

    async def _media_payload(self, message_id: str, file_key: str) -> Any:
        from nahida_bot.agent.media.store import MediaPayload

        data = await self._ensure_client().download_resource(
            message_id=message_id, file_key=file_key, resource_type="file"
        )
        return MediaPayload(
            data=data,
            suffix=_suffix_for(file_name=file_key),
            mime_type="application/octet-stream",
            file_name=file_key,
            file_size=len(data),
        )

    def _media_store(self) -> Any:
        """Return the shared MediaStore, or None when unavailable."""
        getter = getattr(self.api, "get_media_store", None)
        if not callable(getter):
            return None
        try:
            store = getter()
        except Exception:  # noqa: BLE001
            return None
        try:
            from nahida_bot.agent.media.store import MediaStore

            return store if isinstance(store, MediaStore) else None
        except ImportError:
            return None

    async def _cache_inbound_attachments(self, inbound: Any) -> Any:
        """Eagerly download attachments so later turns can access local files."""
        attachments: list[InboundAttachment] = []
        changed = False
        for att in inbound.attachments:
            if att.path or not att.platform_id:
                attachments.append(att)
                continue
            result = await self.download_media(
                att.platform_id,
                file_name=str(att.metadata.get("file_key") or att.alt_text or ""),
            )
            if result is not None and result.path:
                attachments.append(
                    replace(att, path=result.path, mime_type=result.mime_type)
                )
                changed = True
            else:
                attachments.append(att)
        if not changed:
            return inbound
        return replace(inbound, attachments=attachments)

    def _register_file_download_tool(self) -> None:
        async def _handler(*, file_id: str) -> str:
            result = await self.download_media(file_id)
            if result is None:
                return json.dumps(
                    {"error": f"download failed or unknown file_id: {file_id}"}
                )
            return json.dumps(
                {
                    "file_id": file_id,
                    "path": result.path,
                    "file_name": result.file_name,
                    "file_size": result.file_size,
                }
            )

        self.api.register_tool(
            "feishu_download_file",
            "Download an image/file/video from a Feishu message by its "
            "platform_id (message_id:file_key) from an inbound attachment.",
            {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "Feishu attachment platform_id (message_id:file_key).",
                    }
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
            _handler,
        )

    # ── bot identity ──────────────────────────────────────────────

    async def _refresh_bot_info_once(self, *, log_failure: bool) -> bool:
        client = self._ensure_runtime_client()
        try:
            info = await client.get_bot_info()
        except Exception as exc:  # noqa: BLE001
            if log_failure:
                logger.warning(
                    "feishu.bot_info_unavailable",
                    domain=self.config.domain,
                    error=str(exc),
                )
            return False
        open_id = coerce_str(info.get("open_id"))
        if not open_id:
            if log_failure:
                logger.warning("feishu.bot_info_missing_open_id", keys=list(info)[:8])
            return False
        self._self_open_id = open_id
        self._rebuild_converters()
        logger.info(
            "feishu.bot_info_loaded", channel=self.channel_id, self_open_id=open_id
        )
        return True

    def _start_bot_info_retry(self) -> None:
        if self._bot_info_task is not None and not self._bot_info_task.done():
            return
        self._bot_info_task = asyncio.create_task(self._bot_info_retry_loop())

    async def _stop_bot_info_retry(self) -> None:
        task = self._bot_info_task
        self._bot_info_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _bot_info_retry_loop(self) -> None:
        delay = 2.0
        while not self._self_open_id:
            if await self._refresh_bot_info_once(log_failure=False):
                return
            logger.info("feishu.bot_info_retry_scheduled", delay=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    # ── wiring ────────────────────────────────────────────────────

    def _ensure_client(self) -> FeishuClient:
        if self._client is None:
            raise RuntimeError("FeishuPlugin is not loaded: client is unavailable")
        return self._client

    def _ensure_inbound_converter(self) -> FeishuMessageConverter:
        if self._inbound_converter is None:
            raise RuntimeError("FeishuPlugin is not loaded: inbound converter missing")
        return self._inbound_converter

    def _ensure_outbound_converter(self) -> FeishuOutboundConverter:
        if self._outbound_converter is None:
            raise RuntimeError("FeishuPlugin is not loaded: outbound converter missing")
        return self._outbound_converter

    def _ensure_runtime_client(self) -> FeishuClient:
        if self._client is None:
            self._client = FeishuClient(self.config)
        if self._inbound_converter is None or self._outbound_converter is None:
            self._rebuild_converters()
        return self._client

    def _rebuild_converters(self) -> None:
        config = self._config
        self._inbound_converter = FeishuMessageConverter(
            config,
            self_open_id=self._self_open_id,
            name_resolver=self._lookup_member_name,
            observe_untriggered_group_messages=config.group_context_capture,
        )
        self._outbound_converter = FeishuOutboundConverter(config)


def _read_file_bytes(path: str, *, limit: int) -> bytes | None:
    try:
        file = Path(path)
        if file.stat().st_size > limit:
            logger.warning("feishu.attachment_too_large", path=path)
            return None
        return file.read_bytes()
    except OSError as exc:
        logger.warning("feishu.attachment_read_error", path=path, error=str(exc))
        return None


def _post_content_to_plain_text(content: str) -> str:
    """Flatten a post content JSON string back into readable plain text."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return ""
    post = parsed.get("post") if isinstance(parsed, dict) else None
    if not isinstance(post, dict):
        return ""
    body = next(
        (
            post[key]
            for key in ("zh_cn", "en_us", "ja_jp")
            if isinstance(post.get(key), dict)
        ),
        None,
    )
    if body is None:
        for value in post.values():
            if isinstance(value, dict) and isinstance(value.get("content"), list):
                body = value
                break
    if body is None:
        return ""
    paragraphs = body.get("content")
    if not isinstance(paragraphs, list):
        return ""
    lines: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, list):
            continue
        pieces: list[str] = []
        for element in paragraph:
            if not isinstance(element, dict):
                continue
            tag = element.get("tag")
            if tag == "text":
                pieces.append(str(element.get("text") or ""))
            elif tag == "a":
                pieces.append(
                    f"{element.get('text') or ''}({element.get('href') or ''})"
                )
            elif tag == "at":
                pieces.append(
                    f"@{element.get('user_name') or element.get('user_id') or ''}"
                )
            elif tag == "code_block":
                pieces.append(str(element.get("text") or ""))
            elif tag == "hr":
                pieces.append("———")
        lines.append("".join(pieces))
    return "\n".join(line for line in lines if line)


def _suffix_for(*, file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    return suffix if suffix.startswith(".") else ""
