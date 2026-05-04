"""Milky Channel plugin."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.milky.client import (
    MilkyClient,
    MilkyClientError,
    OutgoingSegmentPayload,
)
from nahida_bot.channels.milky._parsing import coerce_int
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
from nahida_bot.core.events import MessagePayload, MessageReceived
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import OutboundMessage, Plugin

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
        if event.get("event_type") != "message_receive":
            logger.debug(
                "milky.event_ignored",
                event_type=event.get("event_type"),
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

        scene = str(data.get("message_scene") or "")
        if scene:
            self._remember_scene(inbound.chat_id, scene)

        session_id = MessageRouter.make_session_id(inbound.platform, inbound.chat_id)
        await self.api.publish_event(
            MessageReceived(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="milky",
            )
        )

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send one normalized outbound message to Milky."""
        client = self._ensure_client()
        converter = self._ensure_outbound_converter()
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
