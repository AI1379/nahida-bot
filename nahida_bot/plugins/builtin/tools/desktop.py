"""Actor-bound Desktop control and screenshot artifact tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.agent.media.store import MediaPayload
from nahida_bot.core.context import current_agent_run, current_session
from nahida_bot.gateway.services.desktop_control import (
    DESKTOP_INPUT_ACTIONS,
    MAX_DESKTOP_EXEC_ARGS,
    MAX_DESKTOP_EXEC_ARG_CHARS,
    MAX_DESKTOP_FILE_READ_BYTES,
    MAX_DESKTOP_HOTKEY_KEYS,
    MAX_DESKTOP_PATH_CHARS,
    MAX_DESKTOP_POMODORO_BREAK_MINUTES,
    MAX_DESKTOP_POMODORO_ROUNDS,
    MAX_DESKTOP_POMODORO_TEXT_CHARS,
    MAX_DESKTOP_POMODORO_WORK_MINUTES,
    MAX_DESKTOP_PROGRAM_CHARS,
    MAX_DESKTOP_SCROLL_STEPS,
    MAX_DESKTOP_TYPED_CHARS,
)
from nahida_bot.plugins.base import Attachment, BotAPI, OutboundMessage
from nahida_bot.plugins.builtin.tools.context import (
    typed_address_from_session_context,
)
from nahida_bot.plugins.tooling import PluginToolDefinition

_logger = structlog.get_logger(__name__)

_MAX_TOOL_RESULT_CHARS = 70_000
_MAX_SCREENSHOT_BASE64_CHARS = 12_000_000
_MAX_SCREENSHOT_BYTES = 9_000_000
_MAX_QUESTION_CHARS = 2_000
_MAX_CAPTION_CHARS = 1_000
_DEFAULT_QUESTION = "Describe the visible desktop and actionable controls."
_SCREENSHOT_MIME_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(frozen=True, slots=True)
class DesktopScreenshotArtifact:
    """A short-lived screenshot cached by the Gateway for one trusted actor."""

    media_id: str
    path: str
    mime_type: str
    file_name: str
    file_size: int
    image_width: int = 0
    image_height: int = 0
    captured_at_ms: int = 0
    coordinate_space: dict[str, Any] | None = None
    data: bytes = b""


class DesktopTools:
    """Tool collection for actor-bound Desktop execution and computer use."""

    def __init__(self, api: BotAPI) -> None:
        self._api = api

    def definitions(self) -> list[PluginToolDefinition]:
        return [
            PluginToolDefinition(
                name="desktop_exec",
                description=(
                    "Run a program on the current actor's Desktop. The call is "
                    "adjudicated locally by Desktop mode and cannot select a node "
                    "or capability. Available in normal chat and scheduled CRON runs."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "program": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_DESKTOP_PROGRAM_CHARS,
                        },
                        "args": {
                            "type": "array",
                            "maxItems": MAX_DESKTOP_EXEC_ARGS,
                            "items": {
                                "type": "string",
                                "maxLength": MAX_DESKTOP_EXEC_ARG_CHARS,
                            },
                            "default": [],
                        },
                        "cwd": {
                            "type": "string",
                            "maxLength": MAX_DESKTOP_PATH_CHARS,
                            "default": "",
                        },
                    },
                    "required": ["program"],
                    "additionalProperties": False,
                },
                handler=self.exec,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="desktop_file_read",
                description=(
                    "Read a bounded byte range on the current actor's Desktop. The "
                    "path is adjudicated locally by Desktop mode and the tool cannot "
                    "select a node or capability."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_DESKTOP_PATH_CHARS,
                        },
                        "root_id": {
                            "type": "string",
                            "maxLength": 128,
                            "default": "",
                        },
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DESKTOP_FILE_READ_BYTES,
                            "default": 65536,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=self.file_read,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="desktop_screenshot_capture",
                description=(
                    "Capture the current actor's Windows virtual desktop into the "
                    "Gateway's short-lived media cache. Returns a media_id that can "
                    "be reused by desktop_screen_observe or desktop_screenshot_send."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=self.screenshot_capture,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="desktop_screen_observe",
                description=(
                    "Analyze a fresh or previously captured Desktop screenshot with "
                    "the configured multimodal fallback model. This is pixels-only: "
                    "it does not use DOM or UI Automation. Coordinates use 0..1000 "
                    "with the origin at top-left."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "maxLength": _MAX_QUESTION_CHARS,
                            "default": _DEFAULT_QUESTION,
                        },
                        "media_id": {
                            "type": "string",
                            "maxLength": 256,
                            "default": "",
                            "description": (
                                "Optional media_id returned by a prior Desktop "
                                "screenshot tool call. Empty captures a fresh image."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
                handler=self.screen_observe,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="desktop_screenshot_send",
                description=(
                    "Send a fresh or previously captured Desktop screenshot to the "
                    "current chat. This tool cannot choose another chat or Desktop."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "media_id": {
                            "type": "string",
                            "maxLength": 256,
                            "default": "",
                        },
                        "caption": {
                            "type": "string",
                            "maxLength": _MAX_CAPTION_CHARS,
                            "default": "",
                        },
                        "attachment_type": {
                            "type": "string",
                            "enum": ["photo", "document"],
                            "default": "photo",
                        },
                    },
                    "additionalProperties": False,
                },
                handler=self.screenshot_send,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="desktop_input",
                description=(
                    "Apply one mouse or keyboard action on the current actor's "
                    "Desktop. Move/click coordinates are normalized from 0 to 1000. "
                    "Observe again after each action."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": sorted(DESKTOP_INPUT_ACTIONS),
                        },
                        "x": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "y": {"type": "integer", "minimum": 0, "maximum": 1000},
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "default": "left",
                        },
                        "clicks": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2,
                            "default": 1,
                        },
                        "scroll_steps": {
                            "type": "integer",
                            "minimum": -MAX_DESKTOP_SCROLL_STEPS,
                            "maximum": MAX_DESKTOP_SCROLL_STEPS,
                            "default": 0,
                        },
                        "text": {
                            "type": "string",
                            "maxLength": MAX_DESKTOP_TYPED_CHARS,
                            "default": "",
                        },
                        "keys": {
                            "type": "array",
                            "maxItems": MAX_DESKTOP_HOTKEY_KEYS,
                            "items": {"type": "string", "maxLength": 16},
                            "default": [],
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                handler=self.input,
                requires_admin=True,
            ),
            PluginToolDefinition(
                name="desktop_pomodoro",
                description=(
                    "Control the current actor's Desktop pomodoro timer. One "
                    "round is a work phase plus a break phase; total_rounds "
                    "rounds run automatically before the timer stops itself. "
                    "Use action=start to begin (optionally with work_minutes/"
                    "break_minutes/total_rounds to configure it first), stop "
                    "to cancel, and status to read the current round and "
                    "remaining time. Reminders pop the pet out with a bubble "
                    "and spoken TTS when speak_reminders is true."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["start", "stop", "toggle", "status", "configure"],
                        },
                        "work_minutes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DESKTOP_POMODORO_WORK_MINUTES,
                        },
                        "break_minutes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DESKTOP_POMODORO_BREAK_MINUTES,
                        },
                        "total_rounds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_DESKTOP_POMODORO_ROUNDS,
                            "description": (
                                "How many work+break rounds one start runs "
                                "before completing."
                            ),
                        },
                        "enabled": {"type": "boolean"},
                        "speak_reminders": {"type": "boolean"},
                        "dynamic_text": {
                            "type": "boolean",
                            "description": (
                                "When true the Gateway model writes each "
                                "reminder line instead of the static texts."
                            ),
                        },
                        "work_start_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_DESKTOP_POMODORO_TEXT_CHARS,
                        },
                        "break_start_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_DESKTOP_POMODORO_TEXT_CHARS,
                        },
                        "break_end_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_DESKTOP_POMODORO_TEXT_CHARS,
                            "description": "Reminder when a break ends but more rounds remain.",
                        },
                        "rounds_done_text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_DESKTOP_POMODORO_TEXT_CHARS,
                            "description": "Reminder when the final round completes.",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                handler=self.pomodoro,
            ),
        ]

    async def exec(
        self, program: str, args: list[str] | None = None, cwd: str = ""
    ) -> str:
        return await self._invoke("exec", program=program, args=args or [], cwd=cwd)

    async def file_read(
        self,
        path: str,
        root_id: str = "",
        offset: int = 0,
        max_bytes: int = 65536,
    ) -> str:
        return await self._invoke(
            "file_read",
            path=path,
            root_id=root_id,
            offset=offset,
            max_bytes=max_bytes,
        )

    async def screenshot_capture(self) -> str:
        context = self._context()
        if isinstance(context, str):
            return context
        artifact = await self._capture(*context)
        if isinstance(artifact, str):
            return artifact
        return self._json({"ok": True, "media": self._public_media(artifact)})

    async def screen_observe(
        self,
        question: str = _DEFAULT_QUESTION,
        media_id: str = "",
    ) -> str:
        if not isinstance(question, str) or len(question) > _MAX_QUESTION_CHARS:
            return self._error(
                "invalid_arguments",
                f"question exceeds {_MAX_QUESTION_CHARS} characters",
            )
        context = self._context()
        if isinstance(context, str):
            return context
        ctx, service = context
        artifact = (
            await self._load(media_id, ctx.actor_account_key)
            if media_id
            else await self._capture(ctx, service)
        )
        if isinstance(artifact, str):
            return artifact

        router_getter = getattr(self._api, "get_model_router", None)
        router: Any = router_getter() if callable(router_getter) else None
        fallback_getter = getattr(
            self._api, "get_multimodal_image_fallback_model", None
        )
        explicit_model = (
            str(fallback_getter() or "") if callable(fallback_getter) else ""
        )
        routed = (
            router.resolve_for_task(
                "desktop_screen_observe",
                explicit=explicit_model,
                default_spec="vision",
                fallback="disabled",
            )
            if router is not None
            else None
        )
        if routed is None:
            return self._error(
                "vision_unavailable",
                "no multimodal fallback or model tagged 'vision' is configured",
            )

        prompt = (
            "Analyze this screenshot of the user's current virtual desktop. "
            "Use only visible pixels; do not assume hidden UI state. Coordinates "
            "must be integer centers in a normalized 0..1000 coordinate space, "
            "with (0,0) at top-left and (1000,1000) at bottom-right. For each "
            "relevant actionable element, report label/appearance, x, y, and "
            "confidence. Mention modal dialogs, sensitive fields, uncertainty, "
            "and whether the requested target is absent.\n\nInspection goal: "
            + (question.strip() or _DEFAULT_QUESTION)
        )
        message = ContextMessage(
            role="user",
            source="desktop_screen_observe",
            content=prompt,
            parts=[
                ContextPart(type="text", text=prompt),
                ContextPart(
                    type="image_base64",
                    data=base64.b64encode(artifact.data).decode("ascii"),
                    mime_type=artifact.mime_type,
                ),
            ],
        )
        try:
            response = await routed.slot.provider.chat(
                messages=[message],
                model=routed.model or routed.slot.default_model,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            _logger.warning(
                "desktop_screen_observe.vision_failed",
                provider_id=routed.slot.id,
                error_type=type(exc).__name__,
            )
            return self._error(
                "vision_failed", f"vision analysis failed: {type(exc).__name__}"
            )
        observation = (response.content or "").strip()
        if not observation:
            return self._error("vision_failed", "vision model returned no observation")
        return self._json(
            {
                "ok": True,
                "observation": observation,
                "coordinateSpace": artifact.coordinate_space,
                "image": {
                    "width": artifact.image_width,
                    "height": artifact.image_height,
                    "capturedAtMs": artifact.captured_at_ms,
                },
                "media": self._public_media(artifact),
            }
        )

    async def screenshot_send(
        self,
        media_id: str = "",
        caption: str = "",
        attachment_type: str = "photo",
    ) -> str:
        if not isinstance(caption, str) or len(caption) > _MAX_CAPTION_CHARS:
            return self._error(
                "invalid_arguments", f"caption exceeds {_MAX_CAPTION_CHARS} characters"
            )
        if attachment_type not in {"photo", "document"}:
            return self._error(
                "invalid_arguments", "attachment_type must be photo or document"
            )
        context = self._context()
        if isinstance(context, str):
            return context
        ctx, service = context
        artifact = (
            await self._load(media_id, ctx.actor_account_key)
            if media_id
            else await self._capture(ctx, service)
        )
        if isinstance(artifact, str):
            return artifact

        extra: dict[str, Any] = {}
        address = typed_address_from_session_context(ctx)
        if address is not None:
            extra["chat_address"] = address.chat_key
        try:
            message_id = await self._api.send_message(
                ctx.chat_id,
                OutboundMessage(
                    text="",
                    extra=extra,
                    attachments=[
                        Attachment(
                            type=attachment_type,
                            path=artifact.path,
                            filename=artifact.file_name,
                            mime_type=artifact.mime_type,
                            caption=caption,
                        )
                    ],
                ),
                channel=ctx.platform,
            )
        except Exception as exc:  # noqa: BLE001 - channel boundary
            _logger.warning(
                "desktop_screenshot_send.failed", error_type=type(exc).__name__
            )
            return self._error(
                "send_failed", f"screenshot delivery failed: {type(exc).__name__}"
            )
        return self._json(
            {
                "ok": True,
                "messageId": message_id,
                "media": self._public_media(artifact),
            }
        )

    async def input(
        self,
        action: str,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
        scroll_steps: int = 0,
        text: str = "",
        keys: list[str] | None = None,
    ) -> str:
        return await self._invoke(
            "input",
            action=action,
            x=x,
            y=y,
            button=button,
            clicks=clicks,
            scroll_steps=scroll_steps,
            text=text,
            keys=keys or [],
        )

    async def pomodoro(
        self,
        action: str,
        work_minutes: int | None = None,
        break_minutes: int | None = None,
        total_rounds: int | None = None,
        enabled: bool | None = None,
        speak_reminders: bool | None = None,
        dynamic_text: bool | None = None,
        work_start_text: str | None = None,
        break_start_text: str | None = None,
        break_end_text: str | None = None,
        rounds_done_text: str | None = None,
    ) -> str:
        return await self._invoke(
            "pomodoro",
            action=action,
            work_minutes=work_minutes,
            break_minutes=break_minutes,
            total_rounds=total_rounds,
            enabled=enabled,
            speak_reminders=speak_reminders,
            dynamic_text=dynamic_text,
            work_start_text=work_start_text,
            break_start_text=break_start_text,
            break_end_text=break_end_text,
            rounds_done_text=rounds_done_text,
        )

    def _context(self) -> tuple[Any, Any] | str:
        run_ctx = current_agent_run.get()
        if run_ctx is not None and run_ctx.depth > 0:
            return self._error(
                "subagent_denied", "Desktop control is unavailable to subagents"
            )
        ctx = current_session.get()
        if ctx is None or not ctx.actor_account_key.strip():
            return self._error(
                "actor_unavailable", "trusted actor identity is unavailable"
            )
        service = getattr(self._api, "desktop_control_service", None)
        if service is None:
            return self._error(
                "service_unavailable", "Desktop control service is unavailable"
            )
        return ctx, service

    async def _invoke(self, operation: str, **arguments: Any) -> str:
        context = self._context()
        if isinstance(context, str):
            return context
        ctx, service = context
        method = getattr(service, operation)
        try:
            result = await method(
                **arguments,
                conversation_id=ctx.effective_conversation_id,
                actor_account_key=ctx.actor_account_key,
                caller=f"agent:{ctx.origin or 'chat'}:{ctx.session_id}",
            )
        except Exception as exc:  # noqa: BLE001 - Desktop service boundary
            _logger.warning(
                "desktop_control.invoke_failed",
                operation=operation,
                error_type=type(exc).__name__,
            )
            return self._error(
                "desktop_unavailable", f"Desktop call failed: {type(exc).__name__}"
            )
        if not result.ok:
            return self._error(result.error_code, result.error_message)
        return self._json({"ok": True, "result": result.payload})

    async def _capture(self, ctx: Any, service: Any) -> DesktopScreenshotArtifact | str:
        store = self._api.get_media_store()
        if store is None:
            return self._error(
                "media_store_unavailable", "Gateway media cache is unavailable"
            )
        try:
            result = await service.screenshot(
                conversation_id=ctx.effective_conversation_id,
                actor_account_key=ctx.actor_account_key,
                caller=f"agent:{ctx.origin or 'chat'}:{ctx.session_id}",
            )
        except Exception as exc:  # noqa: BLE001 - Desktop service boundary
            _logger.warning(
                "desktop_screenshot.capture_failed", error_type=type(exc).__name__
            )
            return self._error(
                "desktop_unavailable",
                f"Desktop screenshot failed: {type(exc).__name__}",
            )
        if not result.ok:
            return self._error(result.error_code, result.error_message)
        payload = result.payload
        encoded = payload.get("data")
        mime_type = payload.get("mimeType")
        if (
            not isinstance(encoded, str)
            or not encoded
            or len(encoded) > _MAX_SCREENSHOT_BASE64_CHARS
            or mime_type not in _SCREENSHOT_MIME_SUFFIXES
        ):
            return self._error(
                "invalid_desktop_response",
                "Desktop returned an invalid or oversized screenshot",
            )
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return self._error(
                "invalid_desktop_response", "Desktop returned invalid screenshot data"
            )
        if not data or len(data) > _MAX_SCREENSHOT_BYTES:
            return self._error(
                "invalid_desktop_response",
                "Desktop returned an invalid or oversized screenshot",
            )

        media_id = self._media_id(ctx.actor_account_key, data)
        captured_at_ms = self._integer(payload.get("capturedAtMs"))
        suffix = _SCREENSHOT_MIME_SUFFIXES[mime_type]
        file_name = f"desktop-screenshot-{captured_at_ms or 'latest'}{suffix}"

        async def loader() -> MediaPayload:
            return MediaPayload(
                data=data,
                suffix=suffix,
                mime_type=mime_type,
                file_name=file_name,
                file_size=len(data),
            )

        try:
            entry = await store.get_or_create(media_id, loader)
        except Exception as exc:  # noqa: BLE001 - media store boundary
            _logger.warning(
                "desktop_screenshot.cache_failed", error_type=type(exc).__name__
            )
            return self._error(
                "media_store_failed",
                f"Gateway could not cache screenshot: {type(exc).__name__}",
            )
        return DesktopScreenshotArtifact(
            media_id=media_id,
            path=entry.path,
            mime_type=mime_type,
            file_name=entry.file_name or file_name,
            file_size=entry.file_size or len(data),
            image_width=self._integer(payload.get("imageWidth")),
            image_height=self._integer(payload.get("imageHeight")),
            captured_at_ms=captured_at_ms,
            coordinate_space=(
                payload.get("coordinateSpace")
                if isinstance(payload.get("coordinateSpace"), dict)
                else None
            ),
            data=data,
        )

    async def _load(
        self, media_id: str, actor_account_key: str
    ) -> DesktopScreenshotArtifact | str:
        if not isinstance(media_id, str) or not media_id.startswith(
            self._media_prefix(actor_account_key)
        ):
            return self._error(
                "media_forbidden", "screenshot media_id does not belong to this actor"
            )
        store = self._api.get_media_store()
        if store is None:
            return self._error(
                "media_store_unavailable", "Gateway media cache is unavailable"
            )
        try:
            entry = await store.get_entry(media_id)
        except Exception as exc:  # noqa: BLE001 - media store boundary
            _logger.warning(
                "desktop_screenshot.cache_read_failed", error_type=type(exc).__name__
            )
            return self._error(
                "media_store_failed",
                f"Gateway could not read screenshot: {type(exc).__name__}",
            )
        if entry is None:
            return self._error(
                "media_not_found", "screenshot media_id is missing or expired"
            )
        mime_type = str(entry.mime_type or "")
        if mime_type not in _SCREENSHOT_MIME_SUFFIXES:
            return self._error(
                "invalid_media", "cached Desktop artifact is not a supported image"
            )
        try:
            data = await asyncio.to_thread(Path(entry.path).read_bytes)
        except OSError:
            return self._error(
                "media_not_found", "cached screenshot file is unavailable"
            )
        if not data or len(data) > _MAX_SCREENSHOT_BYTES:
            return self._error(
                "invalid_media", "cached screenshot is invalid or oversized"
            )
        return DesktopScreenshotArtifact(
            media_id=media_id,
            path=entry.path,
            mime_type=mime_type,
            file_name=entry.file_name
            or f"desktop-screenshot{_SCREENSHOT_MIME_SUFFIXES[mime_type]}",
            file_size=entry.file_size or len(data),
            data=data,
        )

    def _public_media(self, artifact: DesktopScreenshotArtifact) -> dict[str, Any]:
        store = self._api.get_media_store()
        return {
            "mediaId": artifact.media_id,
            "mimeType": artifact.mime_type,
            "fileName": artifact.file_name,
            "fileSize": artifact.file_size,
            "expiresInSeconds": int(getattr(store, "ttl_seconds", 0) or 0),
        }

    @staticmethod
    def _media_prefix(actor_account_key: str) -> str:
        actor_scope = hashlib.sha256(actor_account_key.encode("utf-8")).hexdigest()[:16]
        return f"desktop-screenshot:{actor_scope}:"

    @classmethod
    def _media_id(cls, actor_account_key: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        return f"{cls._media_prefix(actor_account_key)}{digest}"

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _error(cls, code: str, message: str) -> str:
        return cls._json(
            {"ok": False, "error": {"code": code or "failed", "message": message}}
        )

    @classmethod
    def _json(cls, value: dict[str, Any]) -> str:
        try:
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            serialized = json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_desktop_response",
                        "message": "Desktop returned a non-serializable response",
                    },
                },
                sort_keys=True,
            )
        if len(serialized) <= _MAX_TOOL_RESULT_CHARS:
            return serialized
        return json.dumps(
            {
                "ok": bool(value.get("ok")),
                "result": {"truncated": True},
            },
            sort_keys=True,
        )
