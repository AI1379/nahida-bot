"""Milky Channel plugin."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.milky._parsing import as_mapping, coerce_int, coerce_str
from nahida_bot.channels.milky.client import (
    MilkyClient,
    MilkyClientError,
    OutgoingSegmentPayload,
)
from nahida_bot.channels.milky.config import MilkyPluginConfig, parse_milky_config
from nahida_bot.channels.milky.event_stream import MilkyEventStream
from nahida_bot.channels.milky.message_converter import MilkyMessageConverter
from nahida_bot.channels.milky.segment_converter import (
    MilkyOutboundConverter,
    MilkyTargetError,
    fallback_text_for_segments,
    has_rich_segments,
    message_seq_from_send_result,
    resolve_target,
)
from nahida_bot.channels.milky.segments import OutgoingTextSegment
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import MessageObserved, MessagePayload, MessageReceived
from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.core.message_context import (
    chat_context_from_values,
    context_from_inbound,
    sender_context_from_values,
)
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import (
    InboundAttachment,
    InboundMessage,
    MediaDownloadResult,
    OutboundMessage,
    Plugin,
)

if TYPE_CHECKING:
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)


class MilkyPlugin(Plugin):
    """Milky QQ channel plugin."""

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id = manifest.id
        self._config = parse_milky_config(manifest.config)
        self._client: MilkyClient | None = None
        self._event_stream: MilkyEventStream | None = None
        self._inbound_converter: MilkyMessageConverter | None = None
        self._outbound_converter: MilkyOutboundConverter | None = None
        self._self_id = 0
        self._scene_by_peer: OrderedDict[str, str] = OrderedDict()
        self._file_context_cache: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._login_info_task: asyncio.Task[None] | None = None

    @property
    def channel_id(self) -> str:
        """Unique identifier used by the channel registry."""
        return self._channel_id

    @property
    def self_id(self) -> int:
        """Bot QQ ID reported by Milky, if known."""
        return self._self_id

    @property
    def config(self) -> MilkyPluginConfig:
        """Parsed Milky plugin configuration."""
        return self._config

    @property
    def reply_to_inbound(self) -> bool | None:
        """Optional channel override for router default reply-to behavior."""
        return self.config.reply_to_inbound

    async def on_load(self) -> None:
        """Create client, verify connection, and register channel."""
        self._ensure_runtime_client()
        await self._refresh_login_info_once(log_failure=True)
        config = self.config
        logger.info(
            "milky.loaded",
            base_url=config.normalized_base_url,
            api_prefix=config.api_prefix,
            event_path=config.event_path,
            group_trigger_mode=config.group_trigger_mode,
            channel=self.channel_id,
            self_id=self._self_id,
        )
        self.api.register_channel(self)
        self.api.register_prompt_supplement(
            key="no_markdown",
            instruction=(
                "The current channel does not support Markdown rendering. "
                "Do NOT use Markdown formatting such as **bold**, *italic*, "
                "# headings, - bullet lists, > blockquotes, or ```code blocks```. "
                "You MAY use inline LaTeX ($...$) and Markdown tables (| col | col |). "
                "Respond in plain text with minimal formatting."
            ),
            channel=self.channel_id,
        )

    async def on_enable(self) -> None:
        """Start the Milky WebSocket event stream and optional tools."""
        self._ensure_runtime_client()
        if self._self_id <= 0:
            self._start_login_info_retry()
        self._event_stream = MilkyEventStream(self.config, self.handle_inbound_event)
        await self._event_stream.start()
        if self.config.enable_media_download_tool:
            self._register_resource_tool()
            self._register_file_download_tool()
        logger.info("milky.event_stream_started", channel=self.channel_id)

    async def on_disable(self) -> None:
        """Stop event stream and close HTTP client resources."""
        if self._event_stream is not None:
            await self._event_stream.stop()
            self._event_stream = None
        await self._stop_login_info_retry()
        if self._client is not None:
            await self._client.close()
            self._client = None
        logger.info("milky.stopped", channel=self.channel_id)

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Normalize one Milky event and publish a bot event."""
        event_type = str(event.get("event_type") or "")
        if event_type in {"friend_file_upload", "group_file_upload"}:
            await self._handle_file_upload_event(event_type, event)
            return

        if event_type != "message_receive":
            logger.debug(
                "milky.event_ignored",
                event_type=event_type,
                channel=self.channel_id,
            )
            return

        data = event.get("data")
        if not isinstance(data, dict):
            logger.warning(
                "milky.message_event_invalid",
                event_type=event.get("event_type"),
                channel=self.channel_id,
            )
            return
        logger.debug(
            "milky.message_event_received",
            channel=self.channel_id,
            **_message_data_log_fields(data),
        )

        converter = self._ensure_inbound_converter()
        inbound = await converter.to_inbound(data, raw_event=event)
        if inbound is None:
            self._log_converter_drop(data)
            return
        logger.debug(
            "milky.message_normalized",
            channel=self.channel_id,
            **_inbound_log_fields(inbound),
        )

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        logger.debug(
            "milky.message_decision",
            channel=self.channel_id,
            reason=decision.reason,
            observe=decision.observe,
            respond=decision.respond,
            group_trigger_mode=self.config.group_trigger_mode,
            group_context_capture=self.config.group_context_capture,
            **_inbound_log_fields(inbound),
        )
        if not decision.observe:
            logger.debug(
                "milky.message_filtered",
                reason=decision.reason,
                channel=self.channel_id,
                message_scene=coerce_str(data.get("message_scene")),
                peer_id=inbound.chat_id,
                message_seq=inbound.message_id,
                mentions_bot=inbound.mentions_bot,
                mentioned_user_ids=list(inbound.mentioned_user_ids),
            )
            return

        # Resolve file download URLs only for messages that pass the
        # policy gate, so that filtered-out messages never trigger
        # API calls or eager downloads.
        inbound = await self._resolve_file_attachment_urls(inbound)

        scene = str(data.get("message_scene") or "")
        if scene:
            self._remember_scene(inbound.chat_id, scene)

        # Build ChatAddress from scene (group vs private)
        chat_type = "group" if scene == "group" else "private" if scene else "unknown"
        address = ChatAddress(
            channel=inbound.platform,
            target_type=chat_type,
            target_id=inbound.chat_id,
        )
        if not address.is_typed:
            logger.warning(
                "milky.message_scene_missing",
                peer_id=inbound.chat_id,
                channel=self.channel_id,
            )
            return
        session_id = MessageRouter.make_session_id(address)
        event_type = MessageReceived if decision.respond else MessageObserved
        logger.debug(
            "milky.message_publish_start",
            channel=self.channel_id,
            emitted_event=event_type.__name__,
            session_id=session_id,
            decision_reason=decision.reason,
            **_inbound_log_fields(inbound),
        )
        await self.api.publish_event(
            event_type(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="milky",
            )
        )
        logger.debug(
            "milky.message_publish_done",
            channel=self.channel_id,
            emitted_event=event_type.__name__,
            session_id=session_id,
            decision_reason=decision.reason,
            **_inbound_log_fields(inbound),
        )

    async def _handle_file_upload_event(
        self, event_type: str, event: dict[str, Any]
    ) -> None:
        """Convert Milky file upload events into the normal inbound pipeline."""
        data = event.get("data")
        if not isinstance(data, dict):
            logger.warning(
                "milky.file_upload_event_invalid",
                event_type=event_type,
                channel=self.channel_id,
            )
            return
        logger.debug(
            "milky.file_upload_event_received",
            channel=self.channel_id,
            event_type=event_type,
            keys=sorted(data.keys()),
        )

        inbound = self._file_upload_to_inbound(
            event_type,
            data,
            raw_event=event,
        )
        if inbound is None:
            logger.warning(
                "milky.file_upload_event_invalid",
                event_type=event_type,
                channel=self.channel_id,
            )
            return

        # Apply allowlist filter (same logic as converter._is_allowed).
        if inbound.is_group:
            if (
                self._config.allowed_groups
                and inbound.chat_id not in self._config.allowed_groups
            ):
                logger.debug(
                    "milky.file_upload_not_allowed",
                    channel=self.channel_id,
                    chat_id=inbound.chat_id,
                )
                return
        else:
            if (
                self._config.allowed_friends
                and inbound.chat_id not in self._config.allowed_friends
            ):
                logger.debug(
                    "milky.file_upload_not_allowed",
                    channel=self.channel_id,
                    chat_id=inbound.chat_id,
                )
                return

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        logger.debug(
            "milky.file_upload_decision",
            channel=self.channel_id,
            event_type=event_type,
            reason=decision.reason,
            observe=decision.observe,
            respond=decision.respond,
            **_inbound_log_fields(inbound),
        )
        if not decision.observe:
            logger.debug(
                "milky.file_upload_filtered",
                reason=decision.reason,
                channel=self.channel_id,
                peer_id=inbound.chat_id,
                is_group=inbound.is_group,
                message_id=inbound.message_id,
                mentions_bot=inbound.mentions_bot,
            )
            return

        # Resolve file download URLs only for events that pass the
        # policy gate.
        inbound = await self._resolve_file_attachment_urls(inbound)

        scene = "group" if inbound.is_group else "friend"
        self._remember_scene(inbound.chat_id, scene)
        address = ChatAddress(
            channel=inbound.platform,
            target_type="group" if inbound.is_group else "private",
            target_id=inbound.chat_id,
        )
        session_id = MessageRouter.make_session_id(address)
        emitted_event = MessageReceived if decision.respond else MessageObserved
        logger.debug(
            "milky.file_upload_publish_start",
            channel=self.channel_id,
            emitted_event=emitted_event.__name__,
            session_id=session_id,
            **_inbound_log_fields(inbound),
        )
        await self.api.publish_event(
            emitted_event(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="milky",
            )
        )
        logger.debug(
            "milky.file_upload_publish_done",
            channel=self.channel_id,
            emitted_event=emitted_event.__name__,
            session_id=session_id,
            **_inbound_log_fields(inbound),
        )

    def _file_upload_to_inbound(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        raw_event: dict[str, Any],
    ) -> InboundMessage | None:
        is_group = event_type == "group_file_upload"
        group = as_mapping(data.get("group"))
        user = as_mapping(data.get("user"))
        file_data = as_mapping(data.get("file"))

        group_id = coerce_str(
            data.get("group_id")
            or data.get("peer_id")
            or group.get("group_id")
            or group.get("id")
        )
        user_id = coerce_str(
            data.get("user_id")
            or data.get("sender_id")
            or user.get("user_id")
            or user.get("id")
        )
        chat_id = group_id if is_group else user_id
        if not chat_id:
            return None

        file_id = coerce_str(
            data.get("file_id") or file_data.get("file_id") or file_data.get("id")
        )
        file_name = coerce_str(
            data.get("file_name")
            or data.get("name")
            or file_data.get("file_name")
            or file_data.get("name")
        )
        file_size = coerce_int(
            data.get("file_size")
            or data.get("size")
            or file_data.get("file_size")
            or file_data.get("size")
        )
        file_hash = coerce_str(
            data.get("file_hash")
            or data.get("hash")
            or file_data.get("file_hash")
            or file_data.get("hash")
        )
        download_url = coerce_str(
            data.get("download_url")
            or data.get("url")
            or file_data.get("download_url")
            or file_data.get("url")
        )
        if not file_id and not file_name:
            return None

        attachment_metadata: dict[str, object] = {
            "file_name": file_name,
            "file_size": file_size,
            "file_hash": file_hash,
            "milky_event_type": event_type,
        }
        if is_group:
            attachment_metadata["group_id"] = group_id
        else:
            attachment_metadata["user_id"] = user_id

        text = _render_file_upload_text(
            file_name=file_name,
            file_id=file_id,
            file_size=file_size,
        )
        # TODO(milky-media-cache): If file content access is needed, use
        # get_private_file_download_url/get_group_file_download_url to download
        # the file into config.media_download_dir before the URL expires, then
        # persist the stable local path on this attachment.
        attachment = InboundAttachment(
            kind="file",
            platform_id=file_id,
            url=download_url,
            file_size=file_size,
            metadata=attachment_metadata,
        )
        sender_context = sender_context_from_values(
            display_name=coerce_str(
                data.get("sender_name")
                or data.get("nickname")
                or user.get("nickname")
                or user.get("name")
            ),
            platform_user_id=user_id or "0",
            is_self=self._self_id > 0 and user_id == str(self._self_id),
        )
        chat_context = chat_context_from_values(
            platform="milky",
            chat_type="group" if is_group else "private",
            platform_chat_id=chat_id,
            display_name=coerce_str(
                data.get("group_name")
                or data.get("peer_name")
                or data.get("friend_name")
                or group.get("group_name")
                or group.get("name")
                or user.get("nickname")
                or user.get("name")
            ),
        )

        inbound = InboundMessage(
            message_id=coerce_str(
                data.get("message_seq")
                or data.get("event_id")
                or data.get("time")
                or file_id
                or file_name
            ),
            platform="milky",
            chat_id=chat_id,
            user_id=user_id or "0",
            text=text,
            raw_event=raw_event,
            is_group=is_group,
            timestamp=float(coerce_int(data.get("time"))),
            command_prefix=self.config.command_prefix,
            attachments=[attachment],
            sender_context=sender_context,
            chat_context=chat_context,
            mentions_bot=False,
            mentioned_user_ids=(),
        )
        return replace(inbound, message_context=context_from_inbound(inbound))

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send one normalized outbound message to Milky.

        If ``message.reasoning`` is set, it is prepended as a plain-text
        thinking block before the main content.
        """
        logger.debug(
            "milky.send_start",
            channel=self.channel_id,
            target=target,
            **_outbound_log_fields(message),
        )
        client = self._ensure_client()
        converter = self._ensure_outbound_converter()

        # Prepend reasoning as a plain-text thinking block
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
            scene, peer_id = resolve_target(
                target, message, scene_by_peer=self._scene_by_peer
            )
        except MilkyTargetError as exc:
            logger.warning(
                "milky.target_invalid",
                target=target,
                error=str(exc),
                channel=self.channel_id,
            )
            return ""
        segments, file_uploads = converter.to_payload(message)
        logger.debug(
            "milky.send_payload_prepared",
            channel=self.channel_id,
            target=target,
            message_scene=scene,
            peer_id=peer_id,
            segment_count=len(segments),
            file_upload_count=len(file_uploads),
            has_rich_segments=has_rich_segments(segments),
            **_outbound_log_fields(message),
        )
        last_id = ""

        if segments:
            try:
                result = await self._send_segments(client, scene, peer_id, segments)
            except MilkyClientError as exc:
                if not has_rich_segments(segments):
                    raise
                logger.warning(
                    "milky.rich_message_send_failed",
                    target=target,
                    message_scene=scene,
                    peer_id=peer_id,
                    error=str(exc),
                    channel=self.channel_id,
                )
                fallback_text = fallback_text_for_segments(segments)
                if not fallback_text:
                    return ""
                result = await self._send_segments(
                    client, scene, peer_id, [OutgoingTextSegment(fallback_text)]
                )
            last_id = message_seq_from_send_result(result)

        for upload in file_uploads:
            if scene == "group":
                result = await client.upload_group_file(peer_id, upload)
            else:
                result = await client.upload_private_file(peer_id, upload)
            last_id = message_seq_from_send_result(result) or last_id

        logger.debug(
            "milky.send_done",
            channel=self.channel_id,
            target=target,
            message_scene=scene,
            peer_id=peer_id,
            message_id=last_id,
            segment_count=len(segments),
            file_upload_count=len(file_uploads),
        )
        return last_id

    async def _send_segments(
        self,
        client: MilkyClient,
        scene: str,
        peer_id: int,
        segments: Sequence[OutgoingSegmentPayload],
    ) -> dict[str, Any]:
        if scene == "group":
            return await client.send_group_message(peer_id, segments)
        return await client.send_private_message(peer_id, segments)

    def _ensure_client(self) -> MilkyClient:
        if self._client is None:
            raise RuntimeError("MilkyPlugin is not loaded: client is unavailable")
        return self._client

    def _ensure_inbound_converter(self) -> MilkyMessageConverter:
        if self._inbound_converter is None:
            raise RuntimeError("MilkyPlugin is not loaded: inbound converter missing")
        return self._inbound_converter

    def _ensure_outbound_converter(self) -> MilkyOutboundConverter:
        if self._outbound_converter is None:
            raise RuntimeError("MilkyPlugin is not loaded: outbound converter missing")
        return self._outbound_converter

    def _ensure_runtime_client(self) -> MilkyClient:
        if self._client is None:
            self._client = MilkyClient(self.config)
        if self._inbound_converter is None or self._outbound_converter is None:
            self._rebuild_converters()
        return self._client

    async def _refresh_login_info_once(self, *, log_failure: bool) -> bool:
        client = self._ensure_runtime_client()
        try:
            login_info = await client.get_login_info()
        except Exception as exc:  # noqa: BLE001
            if log_failure:
                logger.warning(
                    "milky.login_info_unavailable",
                    base_url=self.config.normalized_base_url,
                    error=str(exc),
                )
            return False

        self._self_id = _pick_int(login_info, "uin", "user_id", "self_id", "qq")
        self._rebuild_converters()
        logger.info(
            "milky.login_info_loaded",
            channel=self.channel_id,
            self_id=self._self_id,
        )
        return True

    def _start_login_info_retry(self) -> None:
        if self._login_info_task is not None and not self._login_info_task.done():
            return
        self._login_info_task = asyncio.create_task(self._login_info_retry_loop())

    async def _stop_login_info_retry(self) -> None:
        task = self._login_info_task
        self._login_info_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _login_info_retry_loop(self) -> None:
        delay = self.config.reconnect_initial_delay
        while self._self_id <= 0:
            if await self._refresh_login_info_once(log_failure=True):
                return
            logger.info("milky.login_info_retry_scheduled", delay=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.config.reconnect_max_delay)

    def _rebuild_converters(self) -> None:
        client = self._ensure_client()
        config = self.config
        self._inbound_converter = MilkyMessageConverter(
            config,
            self_id=self._self_id,
            forward_client=client,
            logger_warning=logger.warning,
            observe_untriggered_group_messages=config.group_context_capture,
        )
        self._outbound_converter = MilkyOutboundConverter(config)

    def _log_converter_drop(self, data: dict[str, Any]) -> None:
        scene = coerce_str(data.get("message_scene"))
        peer_id = coerce_str(data.get("peer_id"))
        message_seq = coerce_str(data.get("message_seq"))
        mentioned_user_ids = _raw_mention_user_ids(data.get("segments"))
        common = {
            "channel": self.channel_id,
            "message_scene": scene,
            "peer_id": peer_id,
            "message_seq": message_seq,
            "group_trigger_mode": self.config.group_trigger_mode,
            "group_context_capture": self.config.group_context_capture,
            "self_id": self._self_id,
            "mentioned_user_ids": list(mentioned_user_ids),
        }
        if (
            scene == "group"
            and self.config.group_trigger_mode == "mention"
            and mentioned_user_ids
            and self._self_id <= 0
        ):
            logger.warning("milky.group_message_dropped_self_id_unknown", **common)
            return
        logger.debug("milky.message_dropped_by_converter", **common)

    def _remember_scene(self, peer_id: str, scene: str) -> None:
        self._scene_by_peer[peer_id] = scene
        self._scene_by_peer.move_to_end(peer_id)
        while len(self._scene_by_peer) > self.config.scene_cache_size:
            self._scene_by_peer.popitem(last=False)

    def _register_resource_tool(self) -> None:
        async def _handler(*, resource_id: str) -> str:
            client = self._ensure_client()
            url = await client.get_resource_temp_url(resource_id)
            return json.dumps(
                {
                    "resource_id": resource_id,
                    "url": url,
                    "expires_hint": self.config.resource_url_ttl_hint,
                }
            )

        self.api.register_tool(
            "milky_get_resource_temp_url",
            "Get a temporary URL for a Milky resource_id from an image, voice, "
            "or video segment.",
            {
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": "Milky resource_id from a media segment.",
                    }
                },
                "required": ["resource_id"],
                "additionalProperties": False,
            },
            _handler,
        )

    # -- file attachment URL resolution ---------------------------------

    _MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB

    async def _resolve_file_attachment_urls(
        self, inbound: InboundMessage
    ) -> InboundMessage:
        """Resolve download URLs for file attachments and cache context.

        Always caches file context so that ``download_media()`` works for
        every file attachment (including file-upload events that already
        carry a ``download_url``).
        """
        resolved: list[InboundAttachment] = []
        changed = False
        for att in inbound.attachments:
            if att.kind == "file" and att.platform_id:
                self._cache_file_context(att)
                if not att.url:
                    url = await self._get_file_download_url(att)
                    if url:
                        att = replace(att, url=url)
                        changed = True
                        logger.debug(
                            "milky.file_url_resolved",
                            file_id=att.platform_id,
                            file_name=att.metadata.get("file_name", ""),
                        )
                # Eager-download for any file attachment that hasn't been
                # persisted yet, regardless of whether the URL came from
                # the event or was just resolved above.
                if self._config.cache_media_on_receive and not att.path:
                    downloaded = await self._eager_download_attachment(att)
                    if downloaded.path:
                        att = downloaded
                        changed = True
            resolved.append(att)
        if not changed:
            return inbound
        return replace(inbound, attachments=resolved)

    async def _eager_download_attachment(
        self, att: InboundAttachment
    ) -> InboundAttachment:
        """Download file content eagerly so the attachment carries a local path."""
        file_name = coerce_str(att.metadata.get("file_name"))
        if att.url:
            # Direct download from existing URL (avoids extra API call)
            result = await self._stream_download_url(
                att.url, file_name=file_name, file_id=att.platform_id
            )
        else:
            result = await self.download_media(att.platform_id, file_name=file_name)
        if result is not None and result.path:
            return replace(
                att,
                path=result.path,
                mime_type=result.mime_type or att.mime_type,
                file_size=result.file_size or att.file_size,
            )
        return att

    def _cache_file_context(self, att: InboundAttachment) -> None:
        """Store file resolution context for later ``download_media()`` calls."""
        meta = att.metadata
        scene = coerce_str(meta.get("_milky_scene"))
        # File upload events carry group_id / user_id directly.
        if not scene:
            if meta.get("group_id"):
                scene = "group"
            elif meta.get("user_id"):
                scene = "friend"
        peer_id = coerce_str(
            meta.get("_milky_peer_id") or meta.get("group_id") or meta.get("user_id")
        )
        if not att.platform_id or not peer_id:
            return
        self._file_context_cache[att.platform_id] = {
            "scene": scene,
            "peer_id": peer_id,
            "file_hash": coerce_str(meta.get("file_hash")),
        }
        self._file_context_cache.move_to_end(att.platform_id)
        while len(self._file_context_cache) > self._config.scene_cache_size:
            self._file_context_cache.popitem(last=False)

    async def _get_file_download_url(self, att: InboundAttachment) -> str:
        """Get a download URL for a file attachment via the Milky API."""
        meta = att.metadata
        scene = coerce_str(meta.get("_milky_scene"))
        if not scene:
            if meta.get("group_id"):
                scene = "group"
            else:
                scene = "friend"
        peer_id_str = coerce_str(
            meta.get("_milky_peer_id") or meta.get("group_id") or meta.get("user_id")
        )
        file_id = att.platform_id
        file_hash = coerce_str(meta.get("file_hash"))

        if not peer_id_str or not file_id:
            return ""

        try:
            peer_id = int(peer_id_str)
        except (ValueError, TypeError):
            return ""

        client = self._ensure_client()
        try:
            if scene == "group":
                return await client.get_group_file_download_url(
                    group_id=peer_id, file_id=file_id
                )
            return await client.get_private_file_download_url(
                user_id=peer_id, file_id=file_id, file_hash=file_hash
            )
        except Exception:
            logger.warning(
                "milky.file_url_failed",
                file_id=file_id,
                scene=scene,
                peer_id=peer_id_str,
            )
            return ""

    async def download_media(
        self, file_id: str, *, file_name: str = ""
    ) -> MediaDownloadResult | None:
        """Download a file from Milky by platform ``file_id``.

        Only accepts Milky file IDs (not arbitrary URLs).  The file context
        must have been cached by a prior inbound message or file-upload event.
        """
        ctx = self._file_context_cache.get(file_id)
        if ctx is None:
            logger.warning(
                "milky.download_media_no_context",
                file_id=file_id,
            )
            return None

        scene = str(ctx.get("scene", ""))
        peer_id_str = str(ctx.get("peer_id", ""))
        file_hash = str(ctx.get("file_hash", ""))

        if not peer_id_str:
            return None

        try:
            peer_id = int(peer_id_str)
        except (ValueError, TypeError):
            return None

        client = self._ensure_client()
        try:
            if scene == "group":
                url = await client.get_group_file_download_url(
                    group_id=peer_id, file_id=file_id
                )
            else:
                url = await client.get_private_file_download_url(
                    user_id=peer_id,
                    file_id=file_id,
                    file_hash=file_hash,
                )
        except Exception:
            logger.warning(
                "milky.download_media_url_failed",
                file_id=file_id,
            )
            return None

        if not url:
            return None

        return await self._stream_download_url(
            url, file_name=file_name, file_id=file_id
        )

    async def _stream_download_url(
        self, url: str, *, file_name: str, file_id: str
    ) -> MediaDownloadResult | None:
        """Download *url* with SSRF protection, size limit, and atomic write."""
        import hashlib
        import ipaddress
        import socket
        import tempfile

        import httpx
        from urllib.parse import urlparse

        # --- SSRF / host validation ---------------------------------------
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if not parsed.hostname:
            return None
        host = parsed.hostname.strip().lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return None
        try:
            addr = ipaddress.ip_address(host)
            if _is_disallowed_ip(addr):
                return None
        except ValueError:
            try:
                infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
            except socket.gaierror:
                return None
            for info in infos:
                raw_addr = info[4][0] if info[4] else ""
                if not raw_addr:
                    continue
                try:
                    if _is_disallowed_ip(ipaddress.ip_address(raw_addr)):
                        return None
                except ValueError:
                    continue

        # --- Build unique destination path --------------------------------
        media_dir = Path(self._config.media_download_dir)
        media_dir.mkdir(parents=True, exist_ok=True)

        hash_prefix = hashlib.sha256((file_id or url).encode()).hexdigest()[:16]
        safe_name = (
            _safe_filename(file_name, fallback="download")
            if file_name
            else _safe_filename(
                _extract_filename_from_url(url),
                fallback=hash_prefix + ".dat",
            )
        )
        dest_path = media_dir / f"{hash_prefix}_{safe_name}"

        # --- Stream to temp file, then atomically rename ------------------
        try:
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                async with http_client.stream(
                    "GET", url, follow_redirects=False
                ) as response:
                    response.raise_for_status()

                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > self._MAX_DOWNLOAD_BYTES:
                                logger.warning(
                                    "milky.download_too_large",
                                    file_id=file_id,
                                    content_length=int(content_length),
                                    max_bytes=self._MAX_DOWNLOAD_BYTES,
                                )
                                return None
                        except ValueError:
                            pass

                    tmp_fd, tmp_path = tempfile.mkstemp(
                        suffix=".tmp", dir=str(media_dir)
                    )
                    download_ok = False
                    try:
                        written = 0
                        async for chunk in response.aiter_bytes():
                            written += len(chunk)
                            if written > self._MAX_DOWNLOAD_BYTES:
                                break
                            os.write(tmp_fd, chunk)
                        else:
                            download_ok = True
                    finally:
                        os.close(tmp_fd)
                        if not download_ok:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass

                    if not download_ok:
                        logger.warning(
                            "milky.download_too_large",
                            file_id=file_id,
                            max_bytes=self._MAX_DOWNLOAD_BYTES,
                        )
                        return None

            os.replace(tmp_path, str(dest_path))
            file_size = dest_path.stat().st_size
            mime_type = response.headers.get("content-type", "")

            logger.info(
                "milky.file_downloaded",
                file_id=file_id,
                path=str(dest_path),
                file_size=file_size,
            )
            return MediaDownloadResult(
                path=str(dest_path),
                file_name=dest_path.name,
                mime_type=mime_type,
                file_size=file_size,
            )
        except Exception as exc:
            logger.warning(
                "milky.download_media_failed",
                file_id=file_id,
                error=str(exc),
            )
            return None

    def _register_file_download_tool(self) -> None:
        """Register ``milky_download_file`` tool for the agent."""

        async def _handler(*, file_id: str, file_name: str = "") -> str:
            result = await self.download_media(file_id, file_name=file_name)
            if result is None:
                return json.dumps({"error": f"Failed to download file: {file_id}"})
            return json.dumps(
                {
                    "path": result.path,
                    "file_name": result.file_name,
                    "mime_type": result.mime_type,
                    "file_size": result.file_size,
                }
            )

        self.api.register_tool(
            "milky_download_file",
            "Download a file from Milky (QQ) by file_id. "
            "The file_id comes from a [File: name=..., file_id=...] segment "
            "in a received message. Returns the local path, file name, "
            "MIME type, and file size of the downloaded file.",
            {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": (
                            "The Milky file_id from a file segment "
                            "in a received message."
                        ),
                    },
                    "file_name": {
                        "type": "string",
                        "description": (
                            "Optional filename hint for the downloaded file."
                        ),
                    },
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
            _handler,
        )


def _pick_int(mapping: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            parsed = coerce_int(value)
            if parsed:
                return parsed
    return 0


def _raw_mention_user_ids(raw_segments: object) -> tuple[str, ...]:
    if not isinstance(raw_segments, list):
        return ()
    ids: list[str] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        if coerce_str(raw.get("type")) not in {"mention", "at"}:
            continue
        data = as_mapping(raw.get("data"))
        for key in ("user_id", "qq", "uin", "target_id", "id"):
            value = coerce_str(data.get(key))
            if value:
                ids.append(value)
                break
    return tuple(dict.fromkeys(ids))


def _message_data_log_fields(data: dict[str, Any]) -> dict[str, object]:
    raw_segments = data.get("segments")
    return {
        "message_scene": coerce_str(data.get("message_scene")),
        "peer_id": coerce_str(data.get("peer_id")),
        "sender_id": coerce_str(data.get("sender_id")),
        "message_seq": coerce_str(data.get("message_seq")),
        "segment_count": len(raw_segments) if isinstance(raw_segments, list) else 0,
        "mentioned_user_ids": list(_raw_mention_user_ids(raw_segments)),
    }


def _inbound_log_fields(inbound: InboundMessage) -> dict[str, object]:
    return {
        "platform": inbound.platform,
        "chat_id": inbound.chat_id,
        "user_id": inbound.user_id,
        "message_id": inbound.message_id,
        "is_group": inbound.is_group,
        "text_chars": len(inbound.text),
        "text_preview": inbound.text[:120],
        "attachment_count": len(inbound.attachments),
        "attachment_kinds": [attachment.kind for attachment in inbound.attachments],
        "mentions_bot": inbound.mentions_bot,
        "mentioned_user_ids": list(inbound.mentioned_user_ids),
        "reply_to": inbound.reply_to,
    }


def _outbound_log_fields(message: OutboundMessage) -> dict[str, object]:
    return {
        "text_chars": len(message.text),
        "text_preview": message.text[:120],
        "reasoning_chars": len(message.reasoning),
        "attachment_count": len(message.attachments),
        "attachment_types": [attachment.type for attachment in message.attachments],
        "reply_to": message.reply_to,
        "extra_keys": sorted(message.extra.keys()),
    }


def _render_file_upload_text(
    *,
    file_name: str,
    file_id: str,
    file_size: int,
) -> str:
    name = file_name or "<unknown>"
    return f"[File: name={name}, file_id={file_id}, size={file_size}]"


def _is_disallowed_ip(addr: object) -> bool:
    """Return True if *addr* is private, loopback, link-local, etc."""
    # Accept both ipaddress objects and raw address strings.
    if isinstance(addr, str):
        import ipaddress

        try:
            addr = ipaddress.ip_address(addr)
        except ValueError:
            return True
    return (
        getattr(addr, "is_private", False)
        or getattr(addr, "is_loopback", False)
        or getattr(addr, "is_link_local", False)
        or getattr(addr, "is_multicast", False)
        or getattr(addr, "is_reserved", False)
        or getattr(addr, "is_unspecified", False)
    )


def _extract_filename_from_url(url: str) -> str:
    """Extract a plausible filename from a URL path."""
    path = re.sub(r"[?#].*$", "", url)
    name = os.path.basename(path)
    if name and "." in name:
        return name
    return ""


def _safe_filename(name: str, *, fallback: str) -> str:
    """Return a single safe filename component for media output."""
    candidate = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    candidate = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", candidate)
    if candidate in {"", ".", ".."}:
        candidate = fallback
    return candidate
