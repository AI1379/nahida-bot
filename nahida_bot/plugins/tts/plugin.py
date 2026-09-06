"""speak plugin: synthesize voice via the unified TTS layer and send it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Coroutine

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.plugins.base import OutboundMessage, Attachment, Plugin
from nahida_bot.plugins.rolling_quota import (
    QuotaReservation,
    RollingQuotaExceeded,
    RollingQuotaLimiter,
)
from nahida_bot.speech import SpeechService, TtsError, parse_tts_config


@dataclass(slots=True, frozen=True)
class SavedAudio:
    """One synthesized audio clip saved into the active workspace."""

    path: Path
    relative_path: str
    mime_type: str
    file_size: int
    voice: str = ""
    text: str = ""


class TtsPlugin(Plugin):
    """Synthesize voice through the unified TTS layer and deliver it."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = parse_tts_config(self.manifest.config)
        self._service: SpeechService | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._task_next_id = 0
        # TODO: persist quota ledger if 24h limits need to survive restarts.
        self._quota = RollingQuotaLimiter(self._config.max_calls_per_24h)

    async def on_load(self) -> None:
        # Prefer the shared Application-level SpeechService (configured via
        # webapi.speech) so the plugin and the Desktop TTS REST surface share
        # one provider client, HTTP session, and semaphore.
        shared = getattr(self.api, "speech_service", None)
        if shared is not None:
            self._service = shared
        elif self._config.backends:
            self._service = SpeechService(self._config)
        else:
            self.api.logger.warning("tts.no_backends_configured")
            return
        self._semaphore = asyncio.Semaphore(self._config.max_concurrency)
        self._register_command()
        self._register_tool()
        self.api.logger.info(
            "tts.loaded",
            backends=list(self._config.backends),
            voices=list(self._config.voices),
            providers=(
                self._service.supported_provider_types() if self._service else []
            ),
        )

    async def on_disable(self) -> None:
        # PluginManager cancels and awaits API-owned tasks before this hook.
        await self._close_service()

    async def on_unload(self) -> None:
        await self._close_service()

    def _register_command(self) -> None:
        names = _configured_command_names(self._config.command_names)
        self.api.register_command(
            names[0],
            self._cmd_speak,
            description="Synthesize a voice message from text and send it to this chat",
            aliases=names[1:],
        )

    def _register_tool(self) -> None:
        self.api.register_tool(
            "speak",
            (
                "Synthesize a voice message from text through the configured TTS "
                "backend and send it to the current chat. The spoken text becomes "
                "part of the conversation (it is remembered). Voice/timbre is bound "
                "to the persona and is NOT selectable. After sending, if you do not "
                "also want a separate text reply, reply exactly NO_REPLY."
            ),
            {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to speak. Prefer short, spoken-style sentences.",
                    },
                    "emotion": {
                        "type": "string",
                        "description": "Optional emotion hint (e.g. happy/sad/calm). Best-effort.",
                    },
                    "text_lang": {
                        "type": "string",
                        "description": "Optional text language code (zh/ja/en). Empty = voice default.",
                    },
                    "send": {
                        "type": "boolean",
                        "description": "Whether to send the voice to the current chat. Default true.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional attachment caption (some channels display it).",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            self._tool_speak,
        )

    async def _cmd_speak(
        self,
        *,
        args: str,
        inbound: Any,
        session_id: str,
    ) -> str:
        text = args.strip()
        if not text:
            return "Usage: /speak <text>"
        session_ctx = current_session.get()
        if session_ctx is None:
            return "Error: No active session context; cannot send voice."
        self._spawn_task(
            "speak",
            self._run_speak_job(text=text, session_ctx=session_ctx),
        )
        return "Voice synthesis started. The voice message will be sent to this chat when ready."

    async def _run_speak_job(self, *, text: str, session_ctx: SessionContext) -> None:
        try:
            token = current_session.set(session_ctx)
            try:
                result = await self._synthesize_and_maybe_send(
                    text=text, send=True, caption="", text_lang=""
                )
            finally:
                current_session.reset(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.api.logger.exception("tts.speak_job_failed", error=str(exc))
            await self._send_text_to_session(
                session_ctx, f"Error: voice synthesis failed: {exc}"
            )
            return
        self.api.logger.info(
            "tts.speak_completed",
            status=result.get("status"),
            sent_message_ids=result.get("sent_message_ids") or [],
        )

    async def _tool_speak(
        self,
        text: str,
        send: bool | None = None,
        caption: str = "",
        text_lang: str = "",
        emotion: str = "",
    ) -> str:
        should_send = self._config.auto_send if send is None else bool(send)
        result = await self._synthesize_and_maybe_send(
            text=text,
            send=should_send,
            caption=caption,
            text_lang=text_lang,
        )
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    async def _synthesize_and_maybe_send(
        self,
        *,
        text: str,
        send: bool,
        caption: str,
        text_lang: str,
    ) -> dict[str, Any]:
        if self._service is None or self._semaphore is None:
            return {"status": "error", "error": "TTS is not enabled or configured."}

        clean_text, truncated = _truncate_text(text, self._config.max_text_length)
        if not clean_text:
            return {"status": "error", "error": "Speech text is empty."}

        try:
            reservation = await self._reserve_quota()
        except RollingQuotaExceeded as exc:
            return {
                "status": "error",
                "code": "tts_quota_exceeded",
                "error": _tts_quota_error(exc),
            }

        committed = False
        try:
            voice_name = self._resolve_voice_name()
            try:
                async with self._semaphore:
                    artifact = await self._service.synthesize(
                        clean_text, voice=voice_name, text_lang=text_lang
                    )
            except TtsError as exc:
                self.api.logger.warning(
                    "tts.synthesis_failed",
                    code=exc.code,
                    retryable=exc.retryable,
                    backend=exc.backend,
                    error=exc.message,
                )
                return await self._degrade_to_text(exc, clean_text, send=send)

            saved = await self._save_audio(artifact)
            sent_message_ids: list[str] = []
            delivered_text = ""
            if send:
                sent_message_ids = await self._send_voice(saved, caption=caption)
                delivered_text = clean_text

            payload: dict[str, Any] = {
                "status": "ok",
                "delivered_text": delivered_text,
                "audio": _audio_payload(saved),
                "media": [_media_payload(saved)],
                "truncated": truncated,
            }
            if sent_message_ids:
                payload["sent_message_ids"] = sent_message_ids
            committed = True
            return payload
        finally:
            if not committed:
                await self._release_quota(reservation)

    async def _degrade_to_text(
        self,
        exc: TtsError,
        text: str,
        *,
        send: bool,
    ) -> dict[str, Any]:
        """Fall back to a plain-text message when synthesis fails (design §10.5)."""
        delivered_text = ""
        if send:
            ctx = current_session.get()
            if ctx is not None:
                await self._send_text_to_session(ctx, text)
                delivered_text = text
            else:
                self.api.logger.warning("tts.degrade_no_session")
        return {
            "status": "degraded",
            "fallback": "text",
            "delivered_text": delivered_text,
            "error": exc.message,
            "code": exc.code,
        }

    async def _save_audio(self, artifact: Any) -> SavedAudio:
        output_dir, relative_dir = self._resolve_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        ext = _extension_for_mime(artifact.mime_type)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha256(
            f"{artifact.voice}\0{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:10]
        filename = f"voice-{timestamp}-{digest}.{ext}"
        path = output_dir / filename
        path.write_bytes(artifact.data)
        relative_path = (relative_dir / filename).as_posix()
        return SavedAudio(
            path=path,
            relative_path=relative_path,
            mime_type=artifact.mime_type,
            file_size=len(artifact.data),
            voice=str(getattr(artifact, "voice", "")),
            text="",
        )

    async def _send_voice(self, audio: SavedAudio, *, caption: str) -> list[str]:
        ctx = current_session.get()
        if ctx is None:
            raise ValueError("No active session context; cannot send voice.")
        extra: dict[str, Any] = {}
        address = _typed_address_from_session_context(ctx)
        if address is not None:
            extra["chat_address"] = address.chat_key
        attachment_type = (
            self._config.attachment_type
            if self._config.attachment_type in {"voice", "audio"}
            else "voice"
        )
        message_id = await self.api.send_message(
            ctx.chat_id,
            OutboundMessage(
                text="",
                extra=extra,
                attachments=[
                    Attachment(
                        type=attachment_type,
                        path=str(audio.path),
                        filename=audio.path.name,
                        mime_type=audio.mime_type,
                        caption=caption,
                    )
                ],
            ),
            channel=ctx.platform,
        )
        return [message_id] if message_id else []

    async def _send_text_to_session(
        self,
        session_ctx: SessionContext,
        text: str,
    ) -> None:
        extra: dict[str, Any] = {}
        address = _typed_address_from_session_context(session_ctx)
        if address is not None:
            extra["chat_address"] = address.chat_key
        await self.api.send_message(
            session_ctx.chat_id,
            OutboundMessage(text=text, extra=extra),
            channel=session_ctx.platform,
        )

    def _resolve_voice_name(self) -> str:
        # Persona → voice mapping lives here. There is no persona system yet,
        # so defer to SpeechService.resolve_voice (default_voice / only voice).
        return ""

    def _resolve_output_dir(self) -> tuple[Path, Path]:
        raw_dir = self._config.output_dir.strip() or "generated/audio"
        relative_dir = Path(raw_dir)
        if (
            relative_dir.is_absolute()
            or relative_dir.drive
            or relative_dir.root
            or any(part == ".." for part in relative_dir.parts)
        ):
            raise ValueError("tts.output_dir must be workspace-relative.")

        ctx = current_session.get()
        workspace_root: str | None = None
        get_workspace_root = getattr(self.api, "get_workspace_root", None)
        if callable(get_workspace_root):
            workspace_id = ctx.workspace_id if ctx is not None else None
            raw_workspace_root = get_workspace_root(workspace_id)
            workspace_root = (
                raw_workspace_root if isinstance(raw_workspace_root, str) else None
            )
        if not workspace_root:
            raise ValueError("Workspace manager is not available.")
        return Path(workspace_root) / relative_dir, relative_dir

    # ── quota ────────────────────────────────────────────────────────────

    async def _reserve_quota(self) -> QuotaReservation | None:
        return await self._quota.reserve()

    async def _release_quota(self, reservation: QuotaReservation | None) -> None:
        await self._quota.release(reservation)

    # ── task lifecycle ───────────────────────────────────────────────────

    def _spawn_task(self, name: str, coro: Coroutine[Any, Any, Any]) -> None:
        self._task_next_id += 1
        task_name = f"{name}-{self._task_next_id}"
        try:
            # The host owns this task and cancels/awaits it before invoking
            # on_disable.  Keeping a second local task registry would make
            # cancellation and error reporting diverge from the host.
            self.api.spawn_task(task_name, coro)
        except BaseException:
            coro.close()
            raise

    async def _close_service(self) -> None:
        if self._service is not None:
            await self._service.close()
            self._service = None
        self._semaphore = None


def _truncate_text(text: str, max_length: int) -> tuple[str, bool]:
    clean = text.strip()
    if len(clean) <= max_length:
        return clean, False
    return clean[:max_length], True


def _audio_payload(audio: SavedAudio) -> dict[str, Any]:
    return {
        "path": audio.relative_path,
        "mime_type": audio.mime_type,
        "file_size": audio.file_size,
        "voice": audio.voice,
    }


def _media_payload(audio: SavedAudio) -> dict[str, Any]:
    return {
        "kind": "audio",
        "path": audio.relative_path,
        "mime_type": audio.mime_type,
        "file_size": audio.file_size,
        "description": "TTS synthesized voice",
        "metadata": {
            "source": "tts",
            "source_tool": "speak",
        },
    }


def _extension_for_mime(mime_type: str) -> str:
    mapping = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/wave": "wav",
        "audio/ogg": "ogg",
        "audio/aac": "aac",
        "audio/mpeg": "mp3",
        "audio/flac": "flac",
        "audio/raw": "raw",
    }
    candidate = mapping.get((mime_type or "").lower())
    if candidate:
        return candidate
    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed.lstrip(".") if guessed else "wav"


def _configured_command_names(raw_names: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        name = raw_name.strip().lstrip("/!")
        if not name or name in seen:
            continue
        names.append(name)
        seen.add(name)
    return names or ["speak"]


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes = (remainder + 59) // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{max(1, minutes)}m"


def _tts_quota_error(exc: RollingQuotaExceeded) -> str:
    return (
        "TTS quota exceeded: "
        f"{exc.used}/{exc.limit} calls used in the last 24 hours. "
        f"Try again in about {_format_duration(exc.retry_after)}."
    )


def _address_from_session_context(ctx: Any) -> ChatAddress:
    address = getattr(ctx, "chat_address", None)
    if isinstance(address, ChatAddress):
        return address
    return ChatAddress.from_inbound(
        str(getattr(ctx, "platform", "")),
        str(getattr(ctx, "chat_id", "")),
    )


def _typed_address_from_session_context(ctx: Any) -> ChatAddress | None:
    address = _address_from_session_context(ctx)
    return address if address.is_typed else None
