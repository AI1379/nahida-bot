"""OneBot Channel plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from nahida_bot.channels.onebot.adapter import OneBotAdapter
from nahida_bot.channels.onebot.config import OneBotPluginConfig, parse_onebot_config
from nahida_bot.channels.onebot.message_converter import OneBotMessageConverter
from nahida_bot.channels.onebot.v11.connect import OneBotV11Connection
from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.events import MessageObserved, MessagePayload, MessageReceived
from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.core.router import MessageRouter
from nahida_bot.plugins.base import (
    MediaDownloadResult,
    OutboundMessage,
    Plugin,
)

if TYPE_CHECKING:
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)

# Max base64 size to inline in a segment (~20 MB). Larger files should
# be uploaded via a separate mechanism. NapCat's default limit is ~30 MB.
_MAX_BASE64_BYTES = 20 * 1024 * 1024


class OneBotPlugin(Plugin):
    """OneBot channel plugin supporting v11 (and eventually v12) protocol."""

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id = manifest.id
        self._config = parse_onebot_config(manifest.config)
        self._adapter = OneBotAdapter(self._config)
        self._converter = OneBotMessageConverter(self._config)
        self._connection: OneBotV11Connection | None = None
        self._self_id = ""

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def config(self) -> OneBotPluginConfig:
        return self._config

    @property
    def self_id(self) -> str:
        return self._self_id

    @property
    def reply_to_inbound(self) -> bool | None:
        return self.config.reply_to_inbound

    async def on_load(self) -> None:
        """Validate config and register channel."""
        logger.info(
            "onebot.config_loaded",
            protocol_version=self._config.protocol_version,
            ws_url=self._config.ws_url,
            webhook_enabled=self._config.webhook_enabled,
            group_trigger_mode=self._config.group_trigger_mode,
            channel=self.channel_id,
        )
        self.api.register_channel(self)

    async def on_enable(self) -> None:
        """Start the OneBot WebSocket connection and register tools."""
        if self._config.ws_url:
            self._connection = OneBotV11Connection(
                self._config,
                on_event=self.handle_inbound_event,
            )
            await self._connection.start()
            logger.info("onebot.event_stream_started", channel=self.channel_id)

        if self._config.enable_media_download_tool:
            self._register_media_tool()

    async def on_disable(self) -> None:
        """Stop the WebSocket connection."""
        if self._connection is not None:
            await self._connection.stop()
            self._connection = None
        logger.info("onebot.stopped", channel=self.channel_id)

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Normalize one OneBot event and publish a bot event."""
        version = self._adapter.detect_version(event)
        if version is None:
            logger.debug("onebot.event_ignored", reason="unrecognized_format")
            return

        protocol = self._adapter.ensure_protocol(version)

        event_type = protocol.detect_event_type(event)
        if event_type is None:
            return

        if not event_type.startswith("message."):
            self._handle_non_message_event(protocol, event, event_type)
            return

        normalized = protocol.normalize_event(event)
        self._update_self_id(normalized)

        is_group = normalized.type == "message.group"
        chat_type = "group" if is_group else "private"

        converter = self._ensure_converter()
        inbound = converter.to_inbound(
            _event_for_converter(normalized),
            is_group=is_group,
            chat_type=chat_type,
        )
        if inbound is None:
            return

        # Cache media attachments on receive
        if self._config.cache_media_on_receive and inbound.attachments:
            await self._cache_inbound_media(inbound.attachments)

        decision = GroupInteractionPolicy(
            mode=self._config.group_trigger_mode,
            observe_untriggered=self._config.group_context_capture,
        ).decide(inbound)
        if not decision.observe:
            return

        address = ChatAddress(
            channel=inbound.platform,
            target_type=chat_type,
            target_id=inbound.chat_id,
        )
        if not address.is_typed:
            logger.warning(
                "onebot.chat_type_unknown",
                chat_id=inbound.chat_id,
                channel=self.channel_id,
            )
            return

        session_id = MessageRouter.make_session_id(address)
        emitted_event = MessageReceived if decision.respond else MessageObserved
        await self.api.publish_event(
            emitted_event(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="onebot",
            )
        )

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send one normalized outbound message via OneBot.

        Handles reasoning, text, images, voice, video, and file attachments.
        Local files are base64-encoded and inlined in the segment payload.
        """
        conn = self._ensure_connection()
        converter = self._ensure_converter()

        if message.reasoning:
            thinking = f"[Thinking]\n{message.reasoning}"
            combined = f"{thinking}\n\n{message.text}" if message.text else thinking
            message = OutboundMessage(
                text=combined,
                reply_to=message.reply_to,
                extra=message.extra,
                attachments=message.attachments,
            )

        segments = converter.to_outbound_segments(message)
        if not segments:
            return ""

        try:
            message_type, peer_id = _parse_target(target, message)
            peer_id_int = int(peer_id)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "onebot.target_invalid",
                target=target,
                error=str(exc),
                channel=self.channel_id,
            )
            return ""

        params: dict[str, Any] = {
            "message_type": message_type,
            "message": segments,
        }
        if message_type == "group":
            params["group_id"] = peer_id_int
        else:
            params["user_id"] = peer_id_int

        try:
            result = await conn.call_action("send_msg", params)
            data = result.get("data", {})
            msg_id = str(data.get("message_id", ""))
            logger.debug(
                "onebot.message_sent",
                target=target,
                message_id=msg_id,
                segment_count=len(segments),
            )
            return msg_id
        except Exception as exc:
            logger.error(
                "onebot.send_failed",
                target=target,
                error=str(exc),
                channel=self.channel_id,
            )
            return ""

    # ── OneBot API helpers ────────────────────────────────

    async def get_login_info(self) -> dict[str, Any]:
        """Get bot's own QQ number and nickname."""
        conn = self._ensure_connection()
        result = await conn.call_action("get_login_info", {})
        return result.get("data", {})

    async def get_group_list(self) -> list[dict[str, Any]]:
        """Get list of groups the bot has joined."""
        conn = self._ensure_connection()
        result = await conn.call_action("get_group_list", {})
        return result.get("data", [])

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        """Get group info by group_id."""
        conn = self._ensure_connection()
        result = await conn.call_action("get_group_info", {"group_id": int(group_id)})
        return result.get("data", {})

    async def get_file_url(self, file_id: str) -> dict[str, Any]:
        """Get a downloadable URL for a file by its platform file_id.

        Calls OneBot ``get_file_url`` (v11 extension) or ``get_file`` (v12).
        Returns the action response data containing the download URL.
        """
        conn = self._ensure_connection()
        try:
            result = await conn.call_action("get_file_url", {"file_id": file_id})
            return result.get("data", {})
        except RuntimeError:
            # Some implementations don't support get_file_url; try get_msg fallback
            return {}

    async def download_media(
        self, file_id: str, file_name: str = ""
    ) -> MediaDownloadResult | None:
        """Download a media file from the OneBot implementation by file_id or URL.

        Uses ``get_file_url`` to obtain a download URL, then fetches the content
        and stores it in ``media_download_dir``.
        """
        url = ""
        if file_id.startswith(("http://", "https://")):
            url = file_id
        else:
            file_info = await self.get_file_url(file_id)
            url = str(file_info.get("url", file_info.get("file", "")))
            if not url:
                logger.warning(
                    "onebot.download_no_url",
                    file_id=file_id,
                    channel=self.channel_id,
                )
                return None

        try:
            import httpx

            media_dir = Path(self._config.media_download_dir)
            media_dir.mkdir(parents=True, exist_ok=True)

            fallback_name = f"{_hash_text(file_id or url)}.dat"
            dest_name = _safe_filename(
                file_name or _extract_filename_from_url(url),
                fallback=fallback_name,
            )
            dest_path = media_dir / dest_name

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                dest_path.write_bytes(response.content)

            file_size = dest_path.stat().st_size
            mime_type = response.headers.get("content-type", "")

            logger.info(
                "onebot.media_downloaded",
                file_id=file_id,
                path=str(dest_path),
                size=file_size,
            )
            return MediaDownloadResult(
                path=str(dest_path),
                file_name=dest_path.name,
                mime_type=mime_type,
                file_size=file_size,
            )
        except Exception as exc:
            logger.error(
                "onebot.media_download_failed",
                file_id=file_id,
                url=url,
                error=str(exc),
            )
            return None

    # ── Private helpers ───────────────────────────────────

    def _handle_non_message_event(
        self,
        protocol: Any,
        event: dict[str, Any],
        event_type: str,
    ) -> None:
        if event_type.startswith("meta."):
            logger.debug(
                "onebot.meta_event",
                event_type=event_type,
                self_id=event.get("self_id", ""),
            )
        elif event_type.startswith("notice."):
            logger.info(
                "onebot.notice_event",
                event_type=event_type,
                sub_type=event.get("sub_type", ""),
                user_id=event.get("user_id", ""),
                group_id=event.get("group_id", ""),
            )
        elif event_type.startswith("request."):
            logger.info(
                "onebot.request_event",
                event_type=event_type,
                sub_type=event.get("sub_type", ""),
                user_id=event.get("user_id", ""),
            )
        else:
            logger.debug("onebot.event_ignored", event_type=event_type)

    def _update_self_id(self, normalized: Any) -> None:
        sid = normalized.self.user_id if hasattr(normalized, "self") else ""
        if sid and str(sid) != self._self_id:
            self._self_id = str(sid)
            self._converter.self_id = str(sid)
            logger.info("onebot.self_id_updated", self_id=self._self_id)

    async def _cache_inbound_media(self, attachments: list[Any]) -> None:
        """Eagerly download media attachments on receive to cache them locally."""
        for att in attachments:
            url = getattr(att, "url", "")
            platform_id = getattr(att, "platform_id", "")
            if not url or not url.startswith(("http://", "https://")):
                continue
            try:
                import httpx

                media_dir = Path(self._config.media_download_dir)
                media_dir.mkdir(parents=True, exist_ok=True)

                dest_name = _safe_filename(
                    platform_id or _extract_filename_from_url(url),
                    fallback=_hash_text(url),
                )
                dest_path = media_dir / f"{dest_name}.cache"

                if dest_path.exists():
                    continue

                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    dest_path.write_bytes(response.content)
                logger.debug(
                    "onebot.media_cached",
                    platform_id=platform_id,
                    path=str(dest_path),
                )
            except Exception:
                logger.debug(
                    "onebot.media_cache_skip",
                    platform_id=platform_id,
                    url=url,
                )

    def _ensure_connection(self) -> OneBotV11Connection:
        if self._connection is None:
            raise RuntimeError("OneBotPlugin is not enabled: connection unavailable")
        return self._connection

    def _ensure_converter(self) -> OneBotMessageConverter:
        return self._converter

    # ── Tool registration ─────────────────────────────────

    def _register_media_tool(self) -> None:
        """Register ``onebot_download_media`` tool for the agent."""

        async def _handler(
            *, file_id: str = "", url: str = "", file_name: str = ""
        ) -> str:
            target = url or file_id
            if not target:
                return json.dumps({"error": "Either file_id or url is required"})

            result = await self.download_media(target, file_name=file_name)
            if result is None:
                return json.dumps({"error": f"Failed to download: {target}"})
            return json.dumps(
                {
                    "path": result.path,
                    "file_name": result.file_name,
                    "mime_type": result.mime_type,
                    "file_size": result.file_size,
                }
            )

        self.api.register_tool(
            "onebot_download_media",
            "Download a media file from OneBot (QQ) by file_id or URL. "
            "Use this when a user sends an image, voice message, video, or "
            "file and you need to access its contents. The file_id or url "
            "can be found in the message attachments metadata. "
            "Returns the local file path and metadata.",
            {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": (
                            "The OneBot file_id or resource_id from an "
                            "inbound media segment (image, record, video, file)."
                        ),
                    },
                    "url": {
                        "type": "string",
                        "description": (
                            "A direct URL to download from, if available "
                            "from the attachment metadata."
                        ),
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Optional filename for the downloaded file.",
                    },
                },
                "additionalProperties": False,
            },
            _handler,
        )


# ── Target parsing ───────────────────────────────────────


def _parse_target(target: str, message: OutboundMessage) -> tuple[str, str]:
    """Parse a target string into (message_type, peer_id).

    Target format: ``"group:123456"`` or ``"private:123456"``.
    Also checks message.extra for target type hints.
    """
    chat_address = message.extra.get("chat_address", "")
    if isinstance(chat_address, str) and chat_address:
        address = ChatAddress.parse(chat_address)
        if address.target_type in {"group", "private"}:
            return address.target_type, address.target_id
        raise ValueError(
            f"Unsupported OneBot chat address target_type: {address.target_type!r}"
        )

    extra_target_type = message.extra.get("target_type", "")
    extra_target_id = message.extra.get("target_id", "")

    if ":" in target:
        parts = target.split(":", 1)
        return _normalize_target_type(parts[0]), parts[1]

    if extra_target_type and extra_target_id:
        return _normalize_target_type(str(extra_target_type)), str(extra_target_id)

    return "private", target


def _normalize_target_type(value: str) -> str:
    if value in {"group", "private"}:
        return value
    if value in {"friend", "user"}:
        return "private"
    raise ValueError(f"Unsupported OneBot target type: {value!r}")


def _event_for_converter(normalized: Any) -> dict[str, Any]:
    """Build converter input that preserves raw fields plus normalized segments."""
    raw = dict(normalized.raw)
    raw["message"] = normalized.message
    raw["alt_message"] = normalized.alt_message
    raw["message_id"] = normalized.message_id or raw.get("message_id", "")
    raw["user_id"] = normalized.user_id or raw.get("user_id", "")
    raw["group_id"] = normalized.group_id or raw.get("group_id", "")
    raw["time"] = normalized.time or raw.get("time", 0)
    return raw


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


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
