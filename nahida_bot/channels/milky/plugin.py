"""Milky Channel plugin."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
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
        config = self.config
        if self._client is None:
            self._client = MilkyClient(config)
        login_info = await self._client.get_login_info()
        self._self_id = _pick_int(login_info, "uin", "user_id", "self_id", "qq")
        self._inbound_converter = MilkyMessageConverter(
            config,
            self_id=self._self_id,
            forward_client=self._client,
            logger_warning=logger.warning,
            observe_untriggered_group_messages=config.group_context_capture,
        )
        self._outbound_converter = MilkyOutboundConverter(config)
        logger.info(
            "milky.connected",
            base_url=config.normalized_base_url,
            api_prefix=config.api_prefix,
            event_path=config.event_path,
            group_trigger_mode=config.group_trigger_mode,
            channel=self.channel_id,
            self_id=self._self_id,
        )
        self.api.register_channel(self)

    async def on_enable(self) -> None:
        """Start the Milky WebSocket event stream and optional tools."""
        self._event_stream = MilkyEventStream(self.config, self.handle_inbound_event)
        await self._event_stream.start()
        if self.config.enable_media_download_tool:
            self._register_resource_tool()
        logger.info("milky.event_stream_started", channel=self.channel_id)

    async def on_disable(self) -> None:
        """Stop event stream and close HTTP client resources."""
        if self._event_stream is not None:
            await self._event_stream.stop()
            self._event_stream = None
        if self._client is not None:
            await self._client.close()
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

        converter = self._ensure_inbound_converter()
        inbound = await converter.to_inbound(data, raw_event=event)
        if inbound is None:
            return

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        if not decision.observe:
            return

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
        await self.api.publish_event(
            event_type(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="milky",
            )
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

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        if not decision.observe:
            return

        scene = "group" if inbound.is_group else "friend"
        self._remember_scene(inbound.chat_id, scene)
        address = ChatAddress(
            channel=inbound.platform,
            target_type="group" if inbound.is_group else "private",
            target_id=inbound.chat_id,
        )
        session_id = MessageRouter.make_session_id(address)
        emitted_event = MessageReceived if decision.respond else MessageObserved
        await self.api.publish_event(
            emitted_event(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="milky",
            )
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


def _pick_int(mapping: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            parsed = coerce_int(value)
            if parsed:
                return parsed
    return 0


def _render_file_upload_text(
    *,
    file_name: str,
    file_id: str,
    file_size: int,
) -> str:
    name = file_name or "<unknown>"
    return f"[File: name={name}, file_id={file_id}, size={file_size}]"
