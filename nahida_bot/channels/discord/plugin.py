"""DiscordPlugin — Discord Bot via discord.py gateway.

Follows the telegram channel's library discipline: discord.py owns the
protocol plumbing (gateway heartbeat/resume, REST rate limits, proxying)
while this plugin owns event semantics, group policy, addressing and
media. The transport (``transport.py``) is the only discord.py touchpoint;
everything here works on plain dicts and is testable without the library.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from nahida_bot.agent.media.store import MediaPayload, MediaStore
from nahida_bot.channels.discord.config import (
    DiscordPluginConfig,
    parse_discord_config,
)
from nahida_bot.channels.discord.message_converter import (
    CONTENT_MESSAGE_TYPES,
    AttachmentUrlCache,
    DiscordMessageConverter,
)
from nahida_bot.core.chat_address import ChatAddress, normalize_target_type
from nahida_bot.core.events import (
    MessageObserved,
    MessagePayload,
    MessageReceived,
    PluginDisabled,
    PluginEnabled,
)
from nahida_bot.core.group_policy import GroupInteractionPolicy
from nahida_bot.core.router import MessageRouter
from nahida_bot_sdk.commands import CommandInfo, CompletionQuery

from nahida_bot.plugins.base import (
    Attachment,
    MediaDownloadResult,
    OutboundMessage,
    Plugin,
)

if TYPE_CHECKING:
    from nahida_bot.channels.discord.transport import DiscordTransport
    from nahida_bot.plugins.base import BotAPI as BotAPIProtocol
    from nahida_bot.plugins.manifest import PluginManifest

logger = structlog.get_logger(__name__)

# Deferred-interaction plumbing: a slash-command reply must reach Discord
# through the interaction followup (or at least resolve the "Bot is
# thinking…" placeholder), otherwise the client eventually reports
# "The application did not respond" once the token expires.
_FOLLOWUP_TOKEN_TTL_SECONDS = 14 * 60  # Discord tokens live 15 minutes
_THINKING_WATCHDOG_SECONDS = 10 * 60  # clean up never-answered placeholders
_PENDING_INTERACTION_CAP = 32
_INTERACTION_REPLY_RE = re.compile(r"^interaction-(\d+)$")


class _PendingInteraction:
    """A deferred interaction whose reply has not been delivered yet."""

    __slots__ = ("object", "registered_at", "watchdog")

    def __init__(
        self, obj: Any, registered_at: float, watchdog: asyncio.Task[None]
    ) -> None:
        self.object = obj
        self.registered_at = registered_at
        self.watchdog = watchdog


class DiscordPlugin(Plugin):
    """Discord Bot channel using a discord.py gateway transport."""

    def __init__(self, api: BotAPIProtocol, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._channel_id = manifest.id
        self._config = parse_discord_config(manifest.config)
        self._transport: DiscordTransport | None = None
        self._gateway_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._converter = DiscordMessageConverter()
        self._attachment_urls = AttachmentUrlCache()
        self._pending_interactions: dict[str, _PendingInteraction] = {}

    @property
    def channel_id(self) -> str:
        """Unique identifier used by the channel registry."""
        return self._channel_id

    @property
    def config(self) -> DiscordPluginConfig:
        """Parsed Discord plugin configuration."""
        return self._config

    @property
    def reply_to_inbound(self) -> bool | None:
        """Optional channel override for router default reply-to behavior."""
        return self.config.reply_to_inbound

    def _create_transport(self) -> DiscordTransport:
        """Build the real transport. Tests override or pre-inject a fake."""
        from nahida_bot.channels.discord.transport import DiscordTransport

        return DiscordTransport(
            token=self._transport_token(),
            proxy=os.environ.get("DISCORD_PROXY") or self.config.proxy,
            on_event=self.handle_inbound_event,
        )

    def _transport_token(self) -> str:
        token = self.config.bot_token
        if not token:
            token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            raise RuntimeError(
                "Discord bot_token not configured. "
                "Set DISCORD_BOT_TOKEN env var or configure discord.bot_token "
                "in config.yaml"
            )
        return token

    async def on_load(self) -> None:
        """Create the transport, verify the token, register the channel."""
        transport = self._create_transport()
        bot_user = await transport.login()
        self._transport = transport
        self._converter = DiscordMessageConverter(
            bot_user_id=bot_user["id"],
            bot_username=bot_user["username"],
        )
        logger.info(
            "discord.connected",
            bot_username=bot_user["username"],
            bot_id=bot_user["id"],
        )
        self.api.register_channel(self)

    async def on_enable(self) -> None:
        """Start the gateway connection and register the download tool."""
        assert self._transport is not None, "Transport not ready — on_load failed?"
        self._gateway_task = asyncio.create_task(self._gateway_loop())
        self._register_download_tool()
        self.api.subscribe(PluginEnabled, self._on_plugin_state_changed)
        self.api.subscribe(PluginDisabled, self._on_plugin_state_changed)
        logger.info("discord.gateway_started")

    async def on_disable(self) -> None:
        """Close the gateway connection."""
        if self._gateway_task is not None:
            self._gateway_task.cancel()
            try:
                await self._gateway_task
            except asyncio.CancelledError:
                pass
            self._gateway_task = None
        for entry in self._pending_interactions.values():
            entry.watchdog.cancel()
        self._pending_interactions.clear()
        if self._transport is not None:
            await self._transport.close()
        logger.info("discord.stopped")

    async def _gateway_loop(self) -> None:
        """Run the gateway connection, reconnecting on transient errors."""
        assert self._transport is not None
        backoff = 1.0
        while True:
            try:
                await self._transport.start()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "discord.gateway_error", error=str(exc), backoff_seconds=backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ── Inbound ───────────────────────────────────────────

    async def handle_inbound_event(self, event: dict[str, Any]) -> None:
        """Dispatch a transport event dict.

        ``message`` events are normalized and published; ``interaction``
        events drive native slash commands; ``ready`` triggers the
        application-command sync.
        """
        kind = event.get("kind")
        if kind == "interaction":
            await self._handle_interaction_event(event.get("interaction") or {})
            return
        if kind == "ready":
            await self._sync_application_commands()
            return
        if kind != "message":
            logger.debug("discord.event_ignored", reason="unknown_kind")
            return
        message = event.get("message")
        if not message or not isinstance(message, dict):
            logger.debug("discord.event_ignored", reason="missing_message")
            return

        author = message.get("author") or {}
        if author.get("bot"):
            logger.debug("discord.message_dropped", reason="bot_author")
            return
        if str(message.get("type", "default")) not in CONTENT_MESSAGE_TYPES:
            logger.debug("discord.message_dropped", reason="system_message")
            return

        self._attachment_urls.record(message.get("attachments") or [])

        inbound = self._converter.to_inbound(message)
        if not inbound.text:
            logger.debug("discord.message_dropped", reason="empty_text")
            return

        gate_reason = self._gate_reason(inbound)
        if gate_reason:
            logger.debug(
                "discord.message_filtered", channel=self.channel_id, reason=gate_reason
            )
            return

        decision = GroupInteractionPolicy(
            mode=self.config.group_trigger_mode,
            observe_untriggered=self.config.group_context_capture,
        ).decide(inbound)
        if not decision.observe:
            logger.debug(
                "discord.message_filtered",
                channel=self.channel_id,
                reason=decision.reason,
            )
            return

        address = ChatAddress(
            channel=inbound.platform,
            target_type=normalize_target_type(
                (inbound.chat_context.chat_type if inbound.chat_context else "")
                or "unknown"
            ),
            target_id=inbound.chat_id,
        )
        session_id = MessageRouter.make_session_id(address)
        event_type = MessageReceived if decision.respond else MessageObserved
        await self.api.publish_event(
            event_type(
                payload=MessagePayload(message=inbound, session_id=session_id),
                source="discord",
            )
        )

    def _gate_reason(self, inbound: Any) -> str:
        """Return a drop reason when allow-lists reject this chat, else ''."""
        chat = inbound.chat_context
        chat_type = chat.chat_type if chat else "unknown"
        if chat_type == "private":
            if (
                self.config.allowed_dm_users
                and inbound.user_id not in self.config.allowed_dm_users
            ):
                return "dm_user_not_allowed"
            return ""
        if self.config.allowed_guilds:
            guild_id = str((inbound.raw_event or {}).get("guild_id", ""))
            if guild_id not in self.config.allowed_guilds:
                return "guild_not_allowed"
        if (
            self.config.blocked_channels
            and inbound.chat_id in self.config.blocked_channels
        ):
            return "channel_blocked"
        return ""

    # ── Interactions (native slash commands) ─────────────

    async def _handle_interaction_event(self, interaction: dict[str, Any]) -> None:
        interaction_type = interaction.get("type")
        if interaction_type == 4:
            await self._handle_autocomplete(interaction)
        elif interaction_type == 2:
            await self._handle_command_invocation(interaction)
        else:
            logger.debug(
                "discord.interaction_ignored",
                reason="unsupported_type",
                interaction_type=interaction_type,
            )

    async def _handle_autocomplete(self, interaction: dict[str, Any]) -> None:
        """Answer an autocomplete interaction within Discord's 3s deadline."""
        if self._transport is None:
            return
        focused = next(
            (o for o in interaction.get("options", []) if o.get("focused")), None
        )
        if focused is None:
            return
        filled = {
            o["name"]: o["value"]
            for o in interaction.get("options", [])
            if not o.get("focused")
        }
        query = CompletionQuery(
            command=str(interaction.get("command_name", "")),
            argument=str(focused.get("name", "")),
            partial=str(focused.get("value", "")),
            filled=filled,
            user_id=str((interaction.get("user") or {}).get("id", "")),
        )
        choices = []
        try:
            choices = await asyncio.wait_for(
                self.api.complete_command_argument(query), timeout=2.5
            )
        except TimeoutError:  # no suggestion beats a timed-out interaction
            pass
        except Exception:  # noqa: BLE001
            logger.exception(
                "discord.autocomplete_failed",
                command=query.command,
                argument=query.argument,
            )
        payload = [
            {
                "name": (choice.display or choice.value)[:100] or "...",
                "value": choice.value[:100],
            }
            for choice in choices[:25]
        ]
        try:
            await self._transport.respond_autocomplete(interaction["_object"], payload)
        except Exception:  # noqa: BLE001 - deadline may already have passed
            logger.warning("discord.autocomplete_response_failed")

    async def _handle_command_invocation(self, interaction: dict[str, Any]) -> None:
        """Translate a slash-command invocation into a normal inbound message.

        Options are re-joined in Discord's order so the freeform args string
        matches text invocation (``/model deepseek-main``), and a synthetic
        bot mention makes the explicit invocation pass group trigger policy.
        """
        if self._transport is None:
            return
        command_name = str(interaction.get("command_name", ""))
        if not command_name:
            return
        values = [str(o.get("value", "")) for o in interaction.get("options", [])]
        content = "/" + command_name + (f" {' '.join(values)}" if values else "")

        guild_id = str(interaction.get("guild_id", "") or "")
        channel_id = str(interaction.get("channel_id", "") or "")
        user = interaction.get("user") or {}
        mentions = []
        if self._converter.bot_user_id:
            mentions = [
                {
                    "id": self._converter.bot_user_id,
                    "name": self._converter.bot_username or self._converter.bot_user_id,
                }
            ]
        message = {
            "id": f"interaction-{interaction.get('id', '')}",
            "type": "default",
            "content": content,
            "timestamp": time.time(),
            "author": {
                "id": str(user.get("id", "0")),
                "name": str(user.get("name", "") or ""),
                "display_name": str(user.get("display_name", "") or ""),
                "bot": bool(user.get("bot", False)),
            },
            "guild_id": guild_id,
            "channel": {
                "id": channel_id,
                "type": "text" if guild_id else "dm",
                "name": "",
                "guild_id": guild_id,
                "parent_id": "",
            },
            "mentions": mentions,
            "mention_everyone": False,
            "attachments": [],
            "embed_count": 0,
            "reference_message_id": "",
        }
        try:
            await self._transport.defer_interaction(interaction["_object"])
        except Exception:  # noqa: BLE001 - already acked or expired
            logger.warning("discord.interaction_defer_failed")
        else:
            self._register_pending_interaction(interaction)
        await self.handle_inbound_event({"kind": "message", "message": message})

    # ── Deferred-interaction followup routing ────────────

    def _register_pending_interaction(self, interaction: dict[str, Any]) -> None:
        """Track a deferred interaction until its reply is delivered."""
        key = str(interaction.get("id", ""))
        obj = interaction.get("_object")
        if not key or obj is None or self._transport is None:
            return
        self._drop_expired_pending_interactions()
        while len(self._pending_interactions) >= _PENDING_INTERACTION_CAP:
            oldest = next(iter(self._pending_interactions))
            self._discard_pending_interaction(oldest)
        watchdog = asyncio.create_task(
            self._resolve_orphaned_interaction(key, _THINKING_WATCHDOG_SECONDS)
        )
        self._pending_interactions[key] = _PendingInteraction(
            obj, time.monotonic(), watchdog
        )

    async def _resolve_orphaned_interaction(self, key: str, delay: float) -> None:
        """Delete the thinking placeholder of an interaction nobody answered."""
        await asyncio.sleep(delay)
        entry = self._pending_interactions.pop(key, None)
        if entry is None or self._transport is None:
            return
        try:
            await self._transport.delete_interaction_response(entry.object)
        except Exception:  # noqa: BLE001 - token may already be gone
            logger.debug("discord.interaction_watchdog_failed", interaction_id=key)

    def _take_pending_interaction(self, reply_to: str) -> Any | None:
        """Consume the pending interaction a reply targets, if still usable."""
        match = _INTERACTION_REPLY_RE.match(reply_to or "")
        if match is None:
            return None
        key = match.group(1)
        entry = self._pending_interactions.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.registered_at > _FOLLOWUP_TOKEN_TTL_SECONDS:
            self._discard_pending_interaction(key)
            return None
        self._discard_pending_interaction(key)
        return entry.object

    def _discard_pending_interaction(self, key: str) -> None:
        entry = self._pending_interactions.pop(key, None)
        if entry is not None:
            entry.watchdog.cancel()

    def _drop_expired_pending_interactions(self) -> None:
        now = time.monotonic()
        for key in [
            key
            for key, entry in self._pending_interactions.items()
            if now - entry.registered_at > _FOLLOWUP_TOKEN_TTL_SECONDS
        ]:
            self._discard_pending_interaction(key)

    # ── Application command sync ─────────────────────────

    async def _sync_application_commands(self) -> None:
        """Push registered bot commands as guild slash commands.

        Guild-scoped registration takes effect instantly; the sync runs on
        gateway ready and whenever plugins (un)register commands.
        """
        if not self.config.register_slash_commands or self._transport is None:
            return
        try:
            infos = self.api.list_commands()
            payload = build_slash_payloads(infos)
            guilds = self.config.allowed_guilds or self._transport.guild_ids()
            for guild_id in guilds:
                await self._transport.sync_guild_commands(guild_id, payload)
            logger.info(
                "discord.commands_synced",
                commands=len(payload),
                guilds=len(guilds),
            )
        except Exception:  # noqa: BLE001 - sync must not take the channel down
            logger.exception("discord.commands_sync_failed")

    async def _on_plugin_state_changed(self, event: Any) -> None:
        """Resync slash commands when another plugin's commands change."""
        if self._gateway_task is not None:
            asyncio.create_task(self._sync_application_commands())

    # ── Outbound ──────────────────────────────────────────

    async def send_message(self, target: str, message: OutboundMessage) -> str:
        """Send a message to a Discord channel/thread/DM channel id.

        Discord renders Markdown natively, so outbound text is passed
        through unchanged and only split at the 2000-character limit.
        Reasoning content is sent as a blockquoted message first.
        A slash-command reply (``reply_to`` is an interaction id) sends
        its first chunk through the interaction followup so Discord's
        "Bot is thinking…" placeholder resolves into the actual reply.
        """
        assert self._transport is not None
        transport = self._transport
        last_msg_id = ""
        followup_object = self._take_pending_interaction(message.reply_to)

        async def _send_text_chunk(text: str, reply_to: str = "") -> str:
            nonlocal followup_object
            if followup_object is not None:
                interaction_object, followup_object = followup_object, None
                try:
                    return await self._send_with_retry(
                        lambda: transport.send_followup(interaction_object, text)
                    )
                except Exception:  # noqa: BLE001 - token may be gone; deliver anyway
                    logger.warning("discord.followup_send_failed")
            return await self._send_with_retry(
                lambda: transport.send_text(target, text, reply_to=reply_to)
            )

        if message.reasoning:
            quoted = "\n".join(
                f"> {line}" for line in message.reasoning.strip().splitlines()
            )
            for chunk in _split_text(quoted, self.config.message_max_length):
                last_msg_id = await _send_text_chunk(chunk)

        if message.text:
            chunks = _split_text(message.text, self.config.message_max_length)
            for i, chunk in enumerate(chunks):
                reply_to = message.reply_to if i == 0 else ""
                last_msg_id = await _send_text_chunk(chunk, reply_to=reply_to)

        for attachment in message.attachments:
            sent_id = await self._send_attachment(target, attachment)
            if sent_id:
                last_msg_id = sent_id

        return last_msg_id

    async def _send_attachment(self, target: str, attachment: Attachment) -> str:
        """Send a single file attachment, returning the message id (or '')."""
        if self._transport is None:
            return ""
        file_path = Path(attachment.path)
        if not file_path.is_file():
            logger.warning("discord.attachment_missing", path=attachment.path)
            return ""
        try:
            return await self._send_with_retry(
                lambda: self._transport.send_file(  # type: ignore[union-attr]
                    target,
                    str(file_path),
                    filename=attachment.filename or file_path.name,
                    caption=attachment.caption or "",
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("discord.attachment_send_failed", path=attachment.path)
            return ""

    async def _send_with_retry(self, send: Any) -> str:
        """Call a transport send with rate-limit retry logic."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return await send()
            except Exception as exc:  # noqa: BLE001
                retry_after = _retry_after_seconds(exc)
                if retry_after is None or attempt >= self.config.send_retry_attempts:
                    raise
                logger.warning(
                    "discord.send_rate_limited",
                    retry_after=retry_after,
                    attempt=attempt,
                    max_attempts=self.config.send_retry_attempts,
                )
                await asyncio.sleep(retry_after)

    # ── Info lookups ──────────────────────────────────────

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """Fetch Discord user profile."""
        if self._transport is None:
            return {}
        try:
            return await self._transport.fetch_user_info(user_id)
        except Exception:  # noqa: BLE001
            return {}

    async def get_group_info(self, group_id: str) -> dict[str, Any]:
        """Fetch Discord channel/thread/guild info."""
        if self._transport is None:
            return {}
        try:
            return await self._transport.fetch_channel_info(group_id)
        except Exception:  # noqa: BLE001
            return {}

    # ── Media Download ────────────────────────────────────

    async def download_media(
        self, file_id: str, destination: str | None = None
    ) -> MediaDownloadResult | None:
        """Download a Discord attachment by attachment id.

        Discord attachment URLs are signed and expire, so the URL captured
        at receive time is used. Downloads go through the shared MediaStore
        when available, with a legacy directory fallback otherwise.
        """
        requested_name = _basename(destination) if destination else ""
        entry = self._attachment_urls.get(file_id)
        if entry is None:
            logger.warning("discord.download_url_unknown", file_id=file_id)
            return None

        store = self._media_store()
        cache_key = f"discord:{self.channel_id}:{file_id}"
        if store is not None:
            existing = await store.get_entry(cache_key)
            if existing is not None and existing.path:
                size = existing.file_size
                try:
                    if not size:
                        size = Path(existing.path).stat().st_size
                except OSError:
                    size = 0
                return MediaDownloadResult(
                    path=existing.path,
                    file_name=requested_name or existing.file_name or file_id,
                    mime_type=existing.mime_type,
                    file_size=size,
                )
            return await self._download_via_cache(
                store, cache_key, entry, file_id=file_id, requested_name=requested_name
            )
        return await self._download_via_legacy_dir(
            entry, file_id=file_id, destination=destination
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

    async def _fetch_attachment_bytes(self, url: str) -> bytes:
        proxy = os.environ.get("DISCORD_PROXY") or self.config.proxy
        async with httpx.AsyncClient(
            proxy=proxy or None, follow_redirects=True, timeout=60.0
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def _download_via_cache(
        self,
        store: MediaStore,
        cache_key: str,
        entry: dict[str, str],
        *,
        file_id: str,
        requested_name: str = "",
    ) -> MediaDownloadResult | None:
        """Download an attachment into the shared MediaStore."""
        display_name = requested_name or entry["filename"] or f"{file_id}.dat"
        try:

            async def loader() -> MediaPayload:
                data = await self._fetch_attachment_bytes(entry["url"])
                return MediaPayload(
                    data=data,
                    suffix=_suffix_from_name(display_name),
                    file_name=display_name,
                    file_size=len(data),
                )

            cached = await store.get_or_create(cache_key, loader)
        except Exception:  # noqa: BLE001
            logger.exception("discord.download_failed", file_id=file_id)
            return None
        logger.info(
            "discord.file_downloaded",
            file_id=file_id,
            path=cached.path,
            file_size=cached.file_size,
        )
        return MediaDownloadResult(
            path=cached.path,
            file_name=requested_name or cached.file_name or display_name,
            mime_type=cached.mime_type,
            file_size=cached.file_size,
        )

    async def _download_via_legacy_dir(
        self,
        entry: dict[str, str],
        *,
        file_id: str,
        destination: str | None,
    ) -> MediaDownloadResult | None:
        """Fallback download into ``media_download_dir`` (no shared cache)."""
        media_dir = self.config.media_download_dir
        dest = (
            Path(destination)
            if destination
            else Path(media_dir) / (f"{file_id}{_suffix_from_name(entry['filename'])}")
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = await self._fetch_attachment_bytes(entry["url"])
            dest.write_bytes(data)
        except Exception:  # noqa: BLE001
            logger.exception("discord.download_failed", file_id=file_id)
            return None
        return MediaDownloadResult(
            path=str(dest),
            file_name=dest.name,
            file_size=dest.stat().st_size,
        )

    def _register_download_tool(self) -> None:
        """Register the download_media tool so the agent can fetch files."""
        import json

        async def _handler(*, file_id: str, file_name: str = "") -> str:
            dest = None
            if file_name:
                media_dir = self.config.media_download_dir
                dest = str(Path(media_dir) / file_name)

            result = await self.download_media(file_id, destination=dest)
            if result is None:
                return json.dumps({"error": f"Failed to download file {file_id}"})
            return json.dumps(
                {
                    "path": result.path,
                    "file_name": result.file_name,
                    "file_size": result.file_size,
                }
            )

        self.api.register_tool(
            "download_media",
            "Download a media file from Discord by attachment id. "
            "Use this when the user sends an image, document, or other file and "
            "you need to access its contents. The attachment id is included in "
            "the message as [Attachment: name=..., type=..., id=...]. "
            "Returns the local file path.",
            {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "The Discord attachment id to download.",
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Optional filename for the downloaded file.",
                    },
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
            _handler,
        )


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most ``limit`` characters.

    Splits on paragraph boundaries first, then single newlines, then hard
    slices oversized lines. Never returns an empty list for empty input.
    """
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        for line in _split_long_block(paragraph, limit):
            chunks.append(line)
    if current:
        chunks.append(current)

    # Merge pass: paragraphs may leave chunks that could still combine.
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) + 2 + len(chunk) <= limit:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)
    return merged


def _split_long_block(block: str, limit: int) -> list[str]:
    """Split one paragraph that exceeds ``limit`` by lines, then hard slices."""
    if len(block) <= limit:
        return [block]
    chunks: list[str] = []
    current = ""
    for line in block.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract a retry-after window from discord.py rate-limit exceptions."""
    value = getattr(exc, "retry_after", None)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


# Discord command/option names: lowercase ascii letters, digits, - and _.
_DISCORD_NAME_RE = re.compile(r"^[-_a-z0-9]{1,32}$")

# nahida-bot argument types → Discord application-command option types.
_OPTION_TYPE_MAP = {
    "string": 3,
    "int": 4,
    "bool": 5,
    "user": 6,
    "channel": 7,
    "float": 10,
}


def build_slash_payloads(infos: list[CommandInfo]) -> list[dict[str, Any]]:
    """Build Discord application-command payloads from command metadata.

    Commands or options whose names do not satisfy Discord's naming rules
    are skipped (with a log) — text invocation keeps working for them.
    """
    payloads: list[dict[str, Any]] = []
    for info in infos:
        if not _DISCORD_NAME_RE.match(info.name):
            logger.warning("discord.command_name_skipped", command=info.name)
            continue
        options: list[dict[str, Any]] = []
        for argument in info.arguments:
            if not _DISCORD_NAME_RE.match(argument.name):
                logger.warning(
                    "discord.option_name_skipped",
                    command=info.name,
                    option=argument.name,
                )
                continue
            option: dict[str, Any] = {
                "name": argument.name,
                "description": (argument.description or "...")[:100],
                "type": _OPTION_TYPE_MAP.get(argument.type, 3),
                "required": argument.required,
            }
            if argument.completer is not None or argument.choices is not None:
                # Static choices register as native autocomplete too, so the
                # enum is offered by Discord's UI without a round trip.
                option["autocomplete"] = True
            options.append(option)
        payload: dict[str, Any] = {
            "name": info.name,
            "description": (info.description or "...")[:100],
            "type": 1,  # CHAT_INPUT (slash) command
            "dm_permission": True,
        }
        if options:
            payload["options"] = options
        payloads.append(payload)
    return payloads


def _basename(value: str) -> str:
    """Return the final path component of *value*."""
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _suffix_from_name(name: str) -> str:
    """Derive a lowercase extension (with leading dot) from a file name/path."""
    candidate = _basename(name)
    if "." not in candidate:
        return ""
    ext = candidate.rsplit(".", 1)[1]
    ext = "".join(ch for ch in ext if ch.isalnum())[:8]
    return f".{ext.lower()}" if ext else ""
