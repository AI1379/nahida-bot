"""Milky Channel plugin."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import structlog

from nahida_bot.channels.milky._parsing import as_mapping, coerce_int, coerce_str
from nahida_bot.channels.milky.client import (
    MilkyClient,
    MilkyClientError,
    OutgoingSegmentPayload,
)
from nahida_bot.channels.milky.config import MilkyPluginConfig, parse_milky_config
from nahida_bot.channels.milky.event_stream import MilkyEventStream
from nahida_bot.channels.milky.file_upload import (
    MilkyFileUpload,
    render_file_upload_text as _render_file_upload_text,
)
from nahida_bot.channels.milky.message_converter import MilkyMessageConverter
from nahida_bot.channels.milky.segment_converter import (
    MilkyOutboundConverter,
    MilkyTargetError,
    fallback_text_for_segments,
    has_rich_segments,
    message_seq_from_send_result,
    resolve_target,
    split_video_segments,
    video_segment_to_file_upload,
)
from nahida_bot.channels.milky.segments import (
    OutgoingTextSegment,
    is_file_only_segments,
    parse_incoming_segments,
)
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import (
    MessageObserved,
    MessagePayload,
    MessageReactionEvent,
    MessageReactionPayload,
    MessageReceived,
    PokeEvent,
    PokePayload,
)
from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.core.router import MessageRouter
from nahida_bot.agent.media.store import MediaPayload, MediaStore
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


class _PendingFileEntry(TypedDict):
    """One queued file delivery awaiting injection into a triggering message."""

    scene: str
    peer_id: str
    file_id: str
    file_name: str
    file_size: int
    file_hash: str
    path: str
    mime_type: str
    source: str
    received_at: float


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
        # Pending file deliveries per chat (key "scene:chat_id"), awaiting a
        # follow-up message that triggers the agent so the file context can be
        # injected into the same turn. Entries expire after
        # config.pending_file_ttl_seconds.
        self._pending_files: OrderedDict[str, list[_PendingFileEntry]] = OrderedDict()
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
        if event_type in {"friend_nudge", "group_nudge"}:
            await self._handle_nudge_event(event_type, event)
            return
        if event_type == "group_message_reaction":
            await self._handle_reaction_event(event)
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

        scene = str(data.get("message_scene") or "")
        if scene:
            self._remember_scene(inbound.chat_id, scene)

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

        # QQ clients cannot attach text alongside a file in the same message
        # (a reply quote plus a file is possible, but that is a content
        # message and goes through the normal path below), so a message whose
        # segments are ALL file segments is a pure file delivery: register it
        # as pending and never trigger the agent by itself. The file is
        # injected into the next text message from the same chat (see
        # _inject_pending_files).
        segments = parse_incoming_segments(data.get("segments"))
        file_only = is_file_only_segments(segments)
        if file_only:
            # Register pending first — subject only to the allowlist, which
            # the converter already applied. The policy gate below only
            # decides whether the delivery is also observed for group context.
            await self._register_inbound_files(inbound)
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
            if not decision.respond:
                # Group context capture: keep the file visible in observed
                # history; private file-only messages publish nothing.
                await self._publish_message_event(
                    inbound,
                    event_cls=MessageObserved,
                    scene=scene,
                )
            return

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
        inbound = await self._resolve_attachment_urls(inbound)

        # Build ChatAddress from scene (group vs private). Validate before
        # consuming pending files so an untyped/unknown scene never swallows
        # them.
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

        # Inject pending files from earlier uploads when this message will
        # run the agent, so the model sees both the file and the follow-up
        # instruction in one turn. Observed (non-triggering) messages leave
        # the pending queue untouched.
        if decision.respond:
            pending = self._consume_pending_files(inbound)
            if pending:
                inbound = self._inject_pending_files(inbound, pending)

        await self._publish_message_event(
            inbound,
            event_cls=MessageReceived if decision.respond else MessageObserved,
            scene=scene,
            decision_reason=decision.reason,
        )

    async def _publish_message_event(
        self,
        inbound: InboundMessage,
        *,
        event_cls: type[MessageReceived] | type[MessageObserved],
        scene: str,
        decision_reason: str = "",
    ) -> None:
        """Build the session id and publish a MessageReceived/MessageObserved."""
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
        logger.debug(
            "milky.message_publish_start",
            channel=self.channel_id,
            emitted_event=event_cls.__name__,
            session_id=session_id,
            decision_reason=decision_reason,
            **_inbound_log_fields(inbound),
        )
        await self.api.publish_event(
            event_cls(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="milky",
            )
        )
        logger.debug(
            "milky.message_publish_done",
            channel=self.channel_id,
            emitted_event=event_cls.__name__,
            session_id=session_id,
            decision_reason=decision_reason,
            **_inbound_log_fields(inbound),
        )

    async def _handle_file_upload_event(
        self, event_type: str, event: dict[str, Any]
    ) -> None:
        """Register a Milky file upload event as pending file delivery.

        File upload events never trigger the agent loop directly. The file is
        downloaded immediately (URLs expire) and queued for injection into the
        next message that triggers the agent in the same chat, so the model
        sees the file together with the follow-up instruction.
        """
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

        scene = "group" if inbound.is_group else "friend"
        self._remember_scene(inbound.chat_id, scene)
        await self._register_inbound_files(inbound)
        logger.debug(
            "milky.file_upload_pending",
            channel=self.channel_id,
            scene=scene,
            peer_id=inbound.chat_id,
            **_inbound_log_fields(inbound),
        )

    async def _handle_nudge_event(self, event_type: str, event: dict[str, Any]) -> None:
        """Publish a PokeEvent for a friend/group nudge targeting the bot.

        Only nudges that target the bot are captured: group nudges where
        ``receiver_id == self_id`` and friend nudges where ``is_self_receive``
        is true. No agent response is triggered; subscribers opt in.
        """
        data = as_mapping(event.get("data"))
        self_id = self._self_id or coerce_int(event.get("self_id")) or 0

        group_id = ""
        is_group = event_type == "group_nudge"
        if is_group:
            group_id = coerce_str(data.get("group_id"))
            sender_id = coerce_str(data.get("sender_id"))
            receiver_id = coerce_str(data.get("receiver_id"))
            if not group_id or not receiver_id:
                logger.debug(
                    "milky.nudge_event_invalid",
                    event_type=event_type,
                    channel=self.channel_id,
                )
                return
            if str(receiver_id) != str(self_id):
                logger.debug(
                    "milky.nudge_not_targeted",
                    channel=self.channel_id,
                    group_id=group_id,
                    receiver_id=receiver_id,
                    self_id=self_id,
                )
                return
            if (
                self._config.allowed_groups
                and group_id not in self._config.allowed_groups
            ):
                logger.debug(
                    "milky.nudge_not_allowed",
                    channel=self.channel_id,
                    group_id=group_id,
                )
                return
            chat_address = ChatAddress(
                channel="milky", target_type="group", target_id=group_id
            )
            user_id = sender_id
            target_user_id = receiver_id
        else:
            user_id = coerce_str(data.get("user_id"))
            if not user_id:
                logger.debug(
                    "milky.nudge_event_invalid",
                    event_type=event_type,
                    channel=self.channel_id,
                )
                return
            if not bool(data.get("is_self_receive")):
                # ``is_self_send`` true means the bot poked someone — outbound.
                logger.debug(
                    "milky.friend_nudge_not_self_receive",
                    channel=self.channel_id,
                    user_id=user_id,
                )
                return
            if (
                self._config.allowed_friends
                and user_id not in self._config.allowed_friends
            ):
                logger.debug(
                    "milky.nudge_not_allowed",
                    channel=self.channel_id,
                    user_id=user_id,
                )
                return
            chat_address = ChatAddress(
                channel="milky", target_type="private", target_id=user_id
            )
            target_user_id = str(self_id) if self_id else user_id

        session_id = MessageRouter.make_session_id(chat_address)
        payload = PokePayload(
            session_id=session_id,
            chat_address=chat_address,
            scene="group" if is_group else "friend",
            group_id=group_id,
            user_id=user_id,
            target_user_id=target_user_id,
            display_action=coerce_str(data.get("display_action")),
            display_suffix=coerce_str(data.get("display_suffix")),
            raw=dict(data),
        )
        logger.debug(
            "milky.nudge_publish_start",
            channel=self.channel_id,
            scene=payload.scene,
            session_id=session_id,
            user_id=user_id,
        )
        await self.api.publish_event(PokeEvent(payload=payload, source="milky"))
        logger.debug(
            "milky.nudge_publish_done",
            channel=self.channel_id,
            session_id=session_id,
        )

    async def _handle_reaction_event(self, event: dict[str, Any]) -> None:
        """Publish a MessageReactionEvent for a group emoji-reply.

        NOTE: there is no cheap filter for reactions on the bot's OWN messages
        (that would require tracking every sent message_seq). All reactions in
        allowed groups are captured; a future subscriber correlates via
        ``message_seq`` against its own sent-message log.
        """
        data = as_mapping(event.get("data"))
        group_id = coerce_str(data.get("group_id"))
        user_id = coerce_str(data.get("user_id"))
        message_seq = coerce_str(data.get("message_seq"))
        if not group_id or not user_id or not message_seq:
            logger.debug(
                "milky.reaction_event_invalid",
                channel=self.channel_id,
            )
            return
        if self._config.allowed_groups and group_id not in self._config.allowed_groups:
            logger.debug(
                "milky.reaction_not_allowed",
                channel=self.channel_id,
                group_id=group_id,
            )
            return
        chat_address = ChatAddress(
            channel="milky", target_type="group", target_id=group_id
        )
        session_id = MessageRouter.make_session_id(chat_address)
        payload = MessageReactionPayload(
            session_id=session_id,
            chat_address=chat_address,
            group_id=group_id,
            user_id=user_id,
            message_seq=message_seq,
            face_id=coerce_str(data.get("face_id")),
            reaction_type=coerce_str(data.get("reaction_type")),
            is_add=bool(data.get("is_add")),
            raw=dict(data),
        )
        logger.debug(
            "milky.reaction_publish_start",
            channel=self.channel_id,
            group_id=group_id,
            message_seq=message_seq,
            user_id=user_id,
        )
        await self.api.publish_event(
            MessageReactionEvent(payload=payload, source="milky")
        )
        logger.debug(
            "milky.reaction_publish_done",
            channel=self.channel_id,
            group_id=group_id,
            message_seq=message_seq,
        )

    _MAX_PENDING_FILES_PER_CHAT = 16

    async def _register_inbound_files(self, inbound: InboundMessage) -> None:
        """Register an inbound's file attachments as pending deliveries.

        Each file is downloaded immediately when possible (temp URLs expire)
        and queued so the next agent-triggering message from the same chat
        can reference it. File context for ``download_media()`` is cached
        from the merged entry so the richer file_hash (upload event) survives
        regardless of which event arrives first.
        """
        scene = "group" if inbound.is_group else "friend"
        for att in inbound.attachments:
            if att.kind != "file" or not att.platform_id:
                continue
            entry = self._register_pending_file(
                scene=scene,
                peer_id=inbound.chat_id,
                file_id=att.platform_id,
                file_name=coerce_str(att.metadata.get("file_name")),
                file_size=att.file_size or coerce_int(att.metadata.get("file_size")),
                file_hash=coerce_str(att.metadata.get("file_hash")),
                source=coerce_str(att.metadata.get("milky_event_type")),
            )
            # Cache context from the merged entry: the message_receive file
            # segment has no file_hash, so caching the raw attachment would
            # overwrite the upload event's hash and break download_media().
            enriched = replace(
                att,
                metadata={
                    **att.metadata,
                    "file_hash": entry["file_hash"],
                },
            )
            self._cache_file_context(enriched)
            await self._download_pending_file(entry)
            logger.debug(
                "milky.file_pending",
                channel=self.channel_id,
                scene=scene,
                peer_id=inbound.chat_id,
                file_id=att.platform_id,
                file_name=entry["file_name"],
                downloaded=bool(entry["path"]),
            )

    def _register_pending_file(
        self,
        *,
        scene: str,
        peer_id: str,
        file_id: str,
        file_name: str,
        file_size: int,
        file_hash: str,
        source: str = "",
    ) -> _PendingFileEntry:
        """Create or merge a pending file entry for (scene, peer, file_id).

        Both the ``message_receive`` file segment and the separate file-upload
        event may carry the same file; the richer one (e.g. the upload event
        with file_hash / a completed download path) fills the gaps.
        """
        self._prune_pending_files()
        key = f"{scene}:{peer_id}"
        entries = self._pending_files.get(key)
        if entries is None:
            entries = []
            self._pending_files[key] = entries
        for entry in entries:
            if entry["file_id"] != file_id:
                continue
            if file_hash and not entry["file_hash"]:
                entry["file_hash"] = file_hash
            if file_name:
                entry["file_name"] = file_name
            if file_size:
                entry["file_size"] = file_size
            if source and not entry["source"]:
                entry["source"] = source
            return entry
        entry: _PendingFileEntry = {
            "scene": scene,
            "peer_id": peer_id,
            "file_id": file_id,
            "file_name": file_name,
            "file_size": file_size,
            "file_hash": file_hash,
            "path": "",
            "mime_type": "",
            "source": source,
            "received_at": time.monotonic(),
        }
        entries.append(entry)
        while len(entries) > self._MAX_PENDING_FILES_PER_CHAT:
            entries.pop(0)
        self._pending_files.move_to_end(key)
        return entry

    async def _download_pending_file(self, entry: _PendingFileEntry) -> None:
        """Download a pending file into the media cache when possible.

        Private files need ``file_hash`` (only the upload event carries it);
        without it the entry stays metadata-only and is completed when the
        matching upload event arrives.
        """
        if entry["path"]:
            return
        file_id = entry["file_id"]
        peer_id_str = entry["peer_id"]
        scene = entry["scene"]
        if not file_id or not peer_id_str:
            return
        try:
            peer_id = int(peer_id_str)
        except (ValueError, TypeError):
            return
        client = self._ensure_client()
        try:
            if scene == "group":
                url = await client.get_group_file_download_url(
                    group_id=peer_id, file_id=file_id
                )
            else:
                file_hash = entry["file_hash"]
                if not file_hash:
                    return
                url = await client.get_private_file_download_url(
                    user_id=peer_id, file_id=file_id, file_hash=file_hash
                )
        except Exception:
            logger.warning(
                "milky.pending_file_url_failed",
                file_id=file_id,
                scene=scene,
                peer_id=peer_id_str,
            )
            return
        if not url:
            return
        result = await self._stream_download_url(
            url,
            file_name=entry["file_name"],
            file_id=file_id,
        )
        if result is not None and result.path:
            entry["path"] = result.path
            entry["mime_type"] = result.mime_type or ""
            if result.file_size:
                entry["file_size"] = result.file_size

    def _consume_pending_files(
        self, inbound: InboundMessage
    ) -> list[_PendingFileEntry]:
        """Pop and return pending files for the inbound's chat (expiry applied)."""
        scene = "group" if inbound.is_group else "friend"
        self._prune_pending_files()
        return self._pending_files.pop(f"{scene}:{inbound.chat_id}", [])

    def _prune_pending_files(self) -> None:
        """Drop pending entries older than the configured TTL."""
        now = time.monotonic()
        ttl = self.config.pending_file_ttl_seconds
        expired_keys = [
            key
            for key, entries in self._pending_files.items()
            if not entries or all(now - entry["received_at"] > ttl for entry in entries)
        ]
        for key in expired_keys:
            del self._pending_files[key]
        for entries in self._pending_files.values():
            entries[:] = [
                entry for entry in entries if now - entry["received_at"] <= ttl
            ]

    def _inject_pending_files(
        self,
        inbound: InboundMessage,
        pending: list[_PendingFileEntry],
    ) -> InboundMessage:
        """Attach pending files to the triggering message.

        The file render is appended to the message text (same representation
        as a native file message) and merged into attachments, deduped by
        file_id so the richer pending entry (local path / file_hash) wins.
        """
        if not pending:
            return inbound
        text_parts = [inbound.text]
        attachments = list(inbound.attachments)
        for entry in pending:
            file_id = entry["file_id"]
            file_name = entry["file_name"]
            file_size = entry["file_size"]
            existing = next(
                (
                    i
                    for i, att in enumerate(attachments)
                    if att.kind == "file" and att.platform_id == file_id
                ),
                None,
            )
            if existing is None:
                text_parts.append(
                    _render_file_upload_text(
                        file_name=file_name,
                        file_id=file_id,
                        file_size=file_size,
                    )
                )
                attachments.append(
                    InboundAttachment(
                        kind="file",
                        platform_id=file_id,
                        path=entry["path"],
                        mime_type=entry["mime_type"],
                        file_size=file_size,
                        metadata={
                            "file_name": file_name,
                            "file_size": file_size,
                            "file_hash": entry["file_hash"],
                            "milky_event_type": entry["source"],
                            "_milky_scene": entry["scene"],
                            "_milky_peer_id": entry["peer_id"],
                        },
                    )
                )
            else:
                # Backfill the existing attachment with the pending entry's
                # richer data (file_hash from the upload event, cached path).
                current = attachments[existing]
                metadata = dict(current.metadata)
                if not metadata.get("file_hash"):
                    metadata["file_hash"] = entry["file_hash"]
                if not metadata.get("file_name"):
                    metadata["file_name"] = file_name
                if not metadata.get("file_size"):
                    metadata["file_size"] = file_size
                if not current.path:
                    metadata["_milky_scene"] = entry["scene"]
                    metadata["_milky_peer_id"] = entry["peer_id"]
                attachments[existing] = replace(
                    current,
                    path=entry["path"] or current.path,
                    mime_type=entry["mime_type"] or current.mime_type,
                    file_size=file_size or current.file_size,
                    metadata=metadata,
                )
        return replace(inbound, text="".join(text_parts), attachments=attachments)

    def _file_upload_to_inbound(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        raw_event: dict[str, Any],
    ) -> InboundMessage | None:
        upload = MilkyFileUpload.from_event(event_type, data)
        if upload is None:
            return None
        return upload.to_inbound(
            raw_event=raw_event,
            command_prefix=self.config.command_prefix,
            self_id=self._self_id,
        )

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
            result: dict[str, Any] | None = None
            try:
                result = await self._send_segments(client, scene, peer_id, segments)
            except MilkyClientError as exc:
                video_segments, non_video = split_video_segments(segments)
                if not video_segments:
                    # No video to rescue — keep the legacy text-placeholder
                    # fallback for image/record/forward failures.
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
                else:
                    # Native video segment send failed — deliver the videos as
                    # files (the verified-working path) and resend any
                    # remaining text/image fallback as a separate message.
                    logger.warning(
                        "milky.video_send_failed_fallback_to_file",
                        target=target,
                        message_scene=scene,
                        peer_id=peer_id,
                        video_count=len(video_segments),
                        error=str(exc),
                        channel=self.channel_id,
                    )
                    for vseg in video_segments:
                        upload = video_segment_to_file_upload(vseg)
                        if scene == "group":
                            vresult = await client.upload_group_file(peer_id, upload)
                        else:
                            vresult = await client.upload_private_file(peer_id, upload)
                        last_id = message_seq_from_send_result(vresult) or last_id
                    fallback_text = fallback_text_for_segments(non_video)
                    if fallback_text:
                        tres = await self._send_segments(
                            client,
                            scene,
                            peer_id,
                            [OutgoingTextSegment(fallback_text)],
                        )
                        last_id = message_seq_from_send_result(tres) or last_id
                    # ``result`` stays None — ``last_id`` already updated above.
            if result is not None:
                last_id = message_seq_from_send_result(result) or last_id

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

    async def _resolve_attachment_urls(self, inbound: InboundMessage) -> InboundMessage:
        """Resolve download/temp URLs for attachments and cache file context.

        - ``file`` attachments: resolve via the group/private file-download
          API and cache file context so ``download_media()`` works later
          (including file-upload events that already carry a ``download_url``).
        - ``image``/``video``/``record`` attachments: upstream sometimes omits
          ``temp_url``; when it is missing, proactively fetch one via
          ``get_resource_temp_url(resource_id)``. Without this, the segment
          carries no URL and the image is silently dropped downstream (#28).

        When ``cache_media_on_receive`` is set, eagerly download every
        unresolved attachment to a local ``path`` so it survives temp-URL
        expiry and can be reconstructed in later turns.
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
            elif att.kind in {"image", "video", "record"} and att.platform_id:
                # Image/voice/video segments use resource_id + temp_url, which
                # upstream may leave empty. Resolve it explicitly so the media
                # is not silently lost (#28 root cause A).
                if not att.url:
                    url = await self._get_media_temp_url(att)
                    if url:
                        att = replace(
                            att,
                            url=url,
                            metadata={**att.metadata, "trusted_url": True},
                        )
                        changed = True
                        logger.debug(
                            "milky.media_url_resolved",
                            kind=att.kind,
                            resource_id=att.platform_id,
                        )
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
        """Download content eagerly so the attachment carries a local path."""
        file_name = coerce_str(att.metadata.get("file_name"))
        if att.url:
            # Direct download from existing URL (avoids extra API call)
            result = await self._stream_download_url(
                att.url, file_name=file_name, file_id=att.platform_id
            )
        elif att.kind == "file":
            # Files without a URL resolve through the cached file-download
            # context populated by _cache_file_context().
            result = await self.download_media(att.platform_id, file_name=file_name)
        else:
            # Image/video/record without a URL: there is no file context to
            # fall back on, so there is nothing to download here. Leave the
            # attachment as-is; downstream surfaces an explicit "image
            # unavailable" notice rather than hallucinating (#28).
            return att
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

    async def _get_media_temp_url(self, att: InboundAttachment) -> str:
        """Resolve a temp URL for an image/video/record ``resource_id``.

        Returns an empty string when Milky has no URL for the resource or the
        call fails; the caller then leaves ``att.url`` empty so downstream can
        surface an explicit "image unavailable" notice instead of hallucinating
        (see SessionRunner._build_vision_parts, #28).
        """
        client = self._ensure_client()
        try:
            url = await client.get_resource_temp_url(att.platform_id)
        except Exception:
            logger.warning(
                "milky.media_url_failed",
                resource_id=att.platform_id,
                kind=att.kind,
            )
            return ""
        if not url:
            logger.debug(
                "milky.media_url_empty",
                resource_id=att.platform_id,
                kind=att.kind,
            )
        return url

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
        """Download *url* with SSRF protection, a hard size limit, and caching.

        Writes through the shared MediaCache when available (so the file is
        TTL-cleaned and deduplicated across receives / the agent resolver),
        and falls back to a direct write into ``media_download_dir`` only
        when no shared cache is configured (e.g. running without a database).
        """
        import hashlib
        import ipaddress
        import socket

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

        store = self._media_store()
        cache_key = (
            f"milky:{self.channel_id}:{file_id}"
            if file_id
            else (
                f"milky:{self.channel_id}:url:"
                f"{hashlib.sha256(url.encode()).hexdigest()}"
            )
        )
        if store is not None:
            return await self._download_via_cache(
                store,
                cache_key,
                url,
                file_name=file_name,
                file_id=file_id,
            )
        return await self._download_via_legacy_dir(
            url, file_name=file_name, file_id=file_id
        )

    def _media_store(self) -> MediaStore | None:
        """Return the shared MediaStore, or None when unavailable."""
        getter = getattr(self.api, "get_media_store", None)
        if not callable(getter):
            return None
        try:
            store = getter()
            return store if isinstance(store, MediaStore) else None
        except Exception:  # noqa: BLE001 - cache access must never break download
            return None

    async def _download_via_cache(
        self,
        store: MediaStore,
        cache_key: str,
        url: str,
        *,
        file_name: str,
        file_id: str,
    ) -> MediaDownloadResult | None:
        """Download *url* into the shared MediaCache and return a result."""
        import httpx

        safe_name = file_name or _extract_filename_from_url(url) or "download"
        try:

            async def loader() -> MediaPayload:
                async with httpx.AsyncClient(timeout=30.0) as http_client:
                    async with http_client.stream(
                        "GET", url, follow_redirects=False
                    ) as response:
                        response.raise_for_status()
                        if not self._check_content_length(response, file_id=file_id):
                            raise RuntimeError("download exceeds size limit")
                        data = await self._read_streamed_body(response, file_id=file_id)
                        if data is None:
                            raise RuntimeError("download exceeds size limit")
                        return MediaPayload(
                            data=data,
                            suffix=_suffix_for(file_name, url),
                            mime_type=response.headers.get("content-type", ""),
                            file_name=safe_name,
                            file_size=len(data),
                        )

            entry = await store.get_or_create(cache_key, loader)
        except Exception as exc:
            logger.warning(
                "milky.download_media_failed",
                file_id=file_id,
                error=str(exc),
            )
            return None

        logger.info(
            "milky.file_downloaded",
            file_id=file_id,
            path=entry.path,
            file_size=entry.file_size,
        )
        return MediaDownloadResult(
            path=entry.path,
            file_name=entry.file_name or safe_name,
            mime_type=entry.mime_type,
            file_size=entry.file_size,
        )

    async def _download_via_legacy_dir(
        self, url: str, *, file_name: str, file_id: str
    ) -> MediaDownloadResult | None:
        """Fallback download into ``media_download_dir`` (no shared cache).

        Only used when the application has no MediaCache (e.g. running without
        a database). This path exists only for standalone plugin tests or
        integrations that do not provide the application media store.
        """
        import hashlib
        import tempfile

        import httpx

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

        try:
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                async with http_client.stream(
                    "GET", url, follow_redirects=False
                ) as response:
                    response.raise_for_status()
                    if not self._check_content_length(response, file_id=file_id):
                        return None
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        suffix=".tmp", dir=str(media_dir)
                    )
                    ok = False
                    try:
                        ok = await self._write_streamed_body(
                            response, tmp_fd, file_id=file_id
                        )
                    finally:
                        os.close(tmp_fd)
                    if not ok:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        return None
                    mime_type = response.headers.get("content-type", "")
            os.replace(tmp_path, str(dest_path))
            file_size = dest_path.stat().st_size
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

    def _check_content_length(self, response: Any, *, file_id: str) -> bool:
        """Return False (and log) if the announced size exceeds the limit."""
        content_length = response.headers.get("content-length")
        if not content_length:
            return True
        try:
            size = int(content_length)
        except ValueError:
            return True
        if size > self._MAX_DOWNLOAD_BYTES:
            logger.warning(
                "milky.download_too_large",
                file_id=file_id,
                content_length=size,
                max_bytes=self._MAX_DOWNLOAD_BYTES,
            )
            return False
        return True

    async def _read_streamed_body(self, response: Any, *, file_id: str) -> bytes | None:
        """Read a streamed response into memory, enforcing the size limit."""
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > self._MAX_DOWNLOAD_BYTES:
                logger.warning(
                    "milky.download_too_large",
                    file_id=file_id,
                    max_bytes=self._MAX_DOWNLOAD_BYTES,
                )
                return None
        return bytes(data)

    async def _write_streamed_body(
        self, response: Any, fd: int, *, file_id: str
    ) -> bool:
        """Stream a response to an open file descriptor with a size limit."""
        written = 0
        async for chunk in response.aiter_bytes():
            written += len(chunk)
            if written > self._MAX_DOWNLOAD_BYTES:
                logger.warning(
                    "milky.download_too_large",
                    file_id=file_id,
                    max_bytes=self._MAX_DOWNLOAD_BYTES,
                )
                return False
            os.write(fd, chunk)
        return True

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


def _suffix_for(file_name: str, url: str) -> str:
    """Derive a lowercase file extension (with leading dot) for a cache entry."""
    for candidate in (file_name, _extract_filename_from_url(url)):
        name = candidate.replace("\\", "/").rsplit("/", 1)[-1]
        if "." not in name:
            continue
        ext = re.sub(r"[^A-Za-z0-9]", "", name.rsplit(".", 1)[1])[:8]
        if ext:
            return f".{ext.lower()}"
    return ""
