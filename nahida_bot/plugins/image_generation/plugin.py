"""Image generation plugin backed by OpenAI-compatible Images APIs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Coroutine

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.plugins.base import Attachment, InboundMessage, OutboundMessage, Plugin
from nahida_bot.plugins.image_generation.client import (
    CodexImageGenerationClient,
    GeneratedImage,
    ImageGenerationError,
    MiniMaxImageGenerationClient,
    OpenAIImageGenerationClient,
)
from nahida_bot.plugins.image_generation.config import (
    CodexImagesBackendConfig,
    MiniMaxBackendConfig,
    OpenAIImagesBackendConfig,
    parse_image_generation_config,
)
from nahida_bot.plugins.rolling_quota import (
    QuotaReservation,
    RollingQuotaExceeded,
    RollingQuotaLimiter,
)


@dataclass(slots=True, frozen=True)
class SavedGeneratedImage:
    """Generated image saved into the active workspace."""

    path: Path
    relative_path: str
    mime_type: str
    file_size: int
    revised_prompt: str = ""
    source: str = ""


ImageQuotaReservation = QuotaReservation


class ImageGenerationPlugin(Plugin):
    """Generate images through an OpenAI-compatible backend."""

    def __init__(self, api: Any, manifest: Any) -> None:
        super().__init__(api, manifest)
        self._config = parse_image_generation_config(self.manifest.config)
        self._clients: dict[
            str,
            OpenAIImageGenerationClient
            | MiniMaxImageGenerationClient
            | CodexImageGenerationClient,
        ] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._task_next_id = 0
        # TODO: Persist this quota ledger if 24h limits need to survive restarts.
        self._image_quota = RollingQuotaLimiter(self._config.max_images_per_24h)

    async def on_load(self) -> None:
        self._register_command()
        self._register_tool()
        self.api.logger.info(
            "image_generation.loaded",
            provider=self._config.provider,
            backend_count=len(self._config.backends),
        )

    async def on_disable(self) -> None:
        # PluginManager cancels and awaits API-owned tasks before this hook.
        await self._close_clients()

    async def on_unload(self) -> None:
        await self._close_clients()

    def _register_command(self) -> None:
        names = _configured_command_names(self._config.command_names)
        self.api.register_command(
            names[0],
            self._cmd_draw,
            description="Start image generation from a prompt and send it to this chat",
            aliases=names[1:],
        )

    def _register_tool(self) -> None:
        self.api.register_tool(
            "image_generate",
            (
                "Generate image files from a text prompt through the configured "
                "OpenAI-compatible image backend. By default it sends the generated "
                "image to the current chat and returns saved workspace paths."
            ),
            {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text prompt describing the image to generate.",
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of images to generate. Default 1.",
                    },
                    "size": {
                        "type": "string",
                        "description": "Optional image size, such as 1024x1024.",
                    },
                    "quality": {
                        "type": "string",
                        "description": "Optional quality value supported by the backend.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional image model override.",
                    },
                    "provider": {
                        "type": "string",
                        "description": (
                            "Optional image generation provider key from "
                            "image_generation.backends."
                        ),
                    },
                    "send": {
                        "type": "boolean",
                        "description": "Whether to send images to the current chat.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption for the sent image attachment.",
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
            self._tool_image_generate,
        )

    async def _cmd_draw(
        self,
        *,
        args: str,
        inbound: InboundMessage,
        session_id: str,
    ) -> str:
        prompt = args.strip()
        if not prompt:
            return "Usage: /draw <prompt>"
        session_ctx = current_session.get()
        if session_ctx is None:
            return "Error: No active session context; cannot send generated image."
        self._spawn_task(
            "draw",
            self._run_draw_job(prompt=prompt, session_ctx=session_ctx),
        )
        return "Image generation started. Generated image will be sent to this chat when ready."

    async def _run_draw_job(self, *, prompt: str, session_ctx: SessionContext) -> None:
        try:
            token = current_session.set(session_ctx)
            try:
                result = await self._generate_and_maybe_send(
                    prompt=prompt,
                    n=1,
                    send=True,
                    caption="",
                    provider="",
                )
            finally:
                current_session.reset(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.api.logger.exception(
                "image_generation.draw_failed",
                error=str(exc),
            )
            await self._send_text_to_session(
                session_ctx,
                f"Error: Image generation failed: {exc}",
            )
            return

        if isinstance(result, str):
            await self._send_text_to_session(session_ctx, result)
            return
        self.api.logger.info(
            "image_generation.draw_completed",
            image_count=len(result["images"]),
            sent_message_ids=result.get("sent_message_ids") or [],
        )

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

    async def _tool_image_generate(
        self,
        prompt: str,
        n: int = 1,
        size: str = "",
        quality: str = "",
        model: str = "",
        provider: str = "",
        send: bool | None = None,
        caption: str = "",
    ) -> str:
        should_send = self._config.auto_send if send is None else bool(send)
        result = await self._generate_and_maybe_send(
            prompt=prompt,
            n=n,
            size=size,
            quality=quality,
            model=model,
            provider=provider,
            send=should_send,
            caption=caption,
        )
        if isinstance(result, str):
            return json.dumps(
                {"status": "error", "error": result},
                ensure_ascii=False,
                sort_keys=True,
            )
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    async def _generate_and_maybe_send(
        self,
        *,
        prompt: str,
        n: int,
        send: bool,
        caption: str,
        size: str = "",
        quality: str = "",
        model: str = "",
        provider: str = "",
    ) -> dict[str, Any] | str:
        reservation: ImageQuotaReservation | None = None
        try:
            selected_provider = provider.strip() or self._config.provider
            images, backend, reservation = await self._generate_and_save(
                prompt=prompt,
                n=n,
                size=size,
                quality=quality,
                model=model,
                provider=selected_provider,
            )
            sent_message_ids: list[str] = []
            if send:
                sent_message_ids = await self._send_images(
                    images,
                    prompt=prompt,
                    model=model.strip() or backend.model,
                    caption=caption,
                )
        except ImageGenerationError as exc:
            await self._release_image_quota(reservation)
            self.api.logger.warning(
                "image_generation.failed",
                code=exc.code,
                retryable=exc.retryable,
                error=exc.message,
            )
            return f"Error: {exc.message}"
        except ValueError as exc:
            await self._release_image_quota(reservation)
            return f"Error: {exc}"
        except BaseException:
            await self._release_image_quota(reservation)
            raise

        payload = {
            "status": "ok",
            "prompt": prompt,
            "provider": selected_provider,
            "backend_type": backend.type,
            "model": model.strip() or backend.model,
            "images": [self._image_payload(image) for image in images],
            "media": [self._media_payload(image) for image in images],
        }
        if sent_message_ids:
            payload["sent_message_ids"] = sent_message_ids
        return payload

    async def _generate_and_save(
        self,
        *,
        prompt: str,
        n: int,
        size: str,
        quality: str,
        model: str,
        provider: str,
    ) -> tuple[
        list[SavedGeneratedImage],
        OpenAIImagesBackendConfig | MiniMaxBackendConfig | CodexImagesBackendConfig,
        ImageQuotaReservation | None,
    ]:
        backend = self._config.backend(provider)
        output_dir, relative_dir = self._resolve_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        client = self._client_for(provider, backend)
        semaphore = self._semaphore_for(provider, backend)
        image_count = _bounded_image_count(n, backend.max_images_per_request)
        reservation = await self._reserve_image_quota(image_count)
        try:
            async with semaphore:
                generated = await client.generate(
                    prompt,
                    model=model,
                    size=size,
                    quality=quality,
                    n=image_count,
                )
            if len(generated) < image_count:
                await self._release_image_quota(
                    reservation,
                    count=image_count - len(generated),
                )

            saved: list[SavedGeneratedImage] = []
            for index, image in enumerate(generated, start=1):
                filename = self._build_filename(prompt, image, index, backend)
                path = output_dir / filename
                path.write_bytes(image.data)
                relative_path = (relative_dir / filename).as_posix()
                saved.append(
                    SavedGeneratedImage(
                        path=path,
                        relative_path=relative_path,
                        mime_type=image.mime_type,
                        file_size=len(image.data),
                        revised_prompt=image.revised_prompt,
                        source=image.source,
                    )
                )
        except BaseException:
            await self._release_image_quota(reservation)
            raise
        return saved, backend, reservation

    async def _reserve_image_quota(
        self,
        count: int,
    ) -> ImageQuotaReservation | None:
        try:
            return await self._image_quota.reserve(count)
        except RollingQuotaExceeded as exc:
            raise ImageGenerationError(
                "image_generation_quota_exceeded",
                (
                    "Image generation quota exceeded: "
                    f"{exc.used}/{exc.limit} images used in the last 24 hours; "
                    f"requested {exc.requested}. "
                    f"Try again in about {_format_duration(exc.retry_after)}."
                ),
            ) from exc

    async def _release_image_quota(
        self,
        reservation: ImageQuotaReservation | None,
        *,
        count: int | None = None,
    ) -> None:
        await self._image_quota.release(reservation, count=count)

    async def _send_images(
        self,
        images: list[SavedGeneratedImage],
        *,
        prompt: str,
        model: str,
        caption: str,
    ) -> list[str]:
        ctx = current_session.get()
        if ctx is None:
            raise ValueError("No active session context; cannot send generated image.")
        extra: dict[str, Any] = {}
        address = _typed_address_from_session_context(ctx)
        if address is not None:
            extra["chat_address"] = address.chat_key
        attachments = [
            Attachment(
                type="photo" if image.mime_type.startswith("image/") else "document",
                path=str(image.path),
                filename=image.path.name,
                mime_type=image.mime_type,
                caption=self._format_caption(
                    caption,
                    prompt=prompt,
                    model=model,
                    image=image,
                ),
            )
            for image in images
        ]
        message_id = await self.api.send_message(
            ctx.chat_id,
            OutboundMessage(text="", extra=extra, attachments=attachments),
            channel=ctx.platform,
        )
        return [message_id] if message_id else []

    def _resolve_output_dir(self) -> tuple[Path, Path]:
        raw_dir = self._config.output_dir.strip() or "generated/images"
        relative_dir = Path(raw_dir)
        if (
            relative_dir.is_absolute()
            or relative_dir.drive
            or relative_dir.root
            or any(part == ".." for part in relative_dir.parts)
        ):
            raise ValueError("image_generation.output_dir must be workspace-relative.")

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

    def _build_filename(
        self,
        prompt: str,
        image: GeneratedImage,
        index: int,
        backend: OpenAIImagesBackendConfig
        | MiniMaxBackendConfig
        | CodexImagesBackendConfig,
    ) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        digest = hashlib.sha256(
            f"{prompt}\0{time.time_ns()}\0{index}".encode("utf-8")
        ).hexdigest()[:10]
        ext = _extension_for_mime(
            image.mime_type, getattr(backend, "output_format", "")
        )
        suffix = f"-{index}" if index > 1 else ""
        return f"image-{timestamp}-{digest}{suffix}.{ext}"

    def _client_for(
        self,
        provider: str,
        backend: OpenAIImagesBackendConfig
        | MiniMaxBackendConfig
        | CodexImagesBackendConfig,
    ) -> (
        OpenAIImageGenerationClient
        | MiniMaxImageGenerationClient
        | CodexImageGenerationClient
    ):
        existing = self._clients.get(provider)
        if existing is not None:
            return existing
        if backend.type == "minimax":
            client: (
                OpenAIImageGenerationClient
                | MiniMaxImageGenerationClient
                | CodexImageGenerationClient
            ) = MiniMaxImageGenerationClient(backend)
        elif backend.type == "codex-images":
            resolver = self._build_codex_token_resolver(backend)
            client = CodexImageGenerationClient(backend, resolver)
        elif backend.type == "openai-images":
            client = OpenAIImageGenerationClient(backend)
        else:
            raise ValueError(
                f"Unsupported image generation backend type '{backend.type}'. "
                "Supported: 'openai-images', 'minimax', 'codex-images'."
            )
        self._clients[provider] = client
        return client

    def _build_codex_token_resolver(
        self, backend: CodexImagesBackendConfig
    ) -> Callable[[], Awaitable[Any]]:
        """Return an async callable that resolves a fresh Codex OAuth token.

        Looks up the configured ``type: codex`` LLM provider via the
        ProviderManager and delegates to its ``_resolve_token()`` so refresh
        stays centralized there.
        """

        async def resolver() -> Any:
            get_manager = getattr(self.api, "get_provider_manager", None)
            manager: Any = get_manager() if callable(get_manager) else None
            if manager is None:
                raise ImageGenerationError(
                    "image_generation_not_configured",
                    "Provider manager is not available; cannot resolve Codex token.",
                )
            slot = manager.get(backend.provider_id)  # type: ignore[union-attr]
            if slot is None:
                raise ImageGenerationError(
                    "image_generation_not_configured",
                    f"Codex provider '{backend.provider_id}' is not configured. "
                    "Add a 'type: codex' provider entry and run "
                    f"`nahida-bot auth login {backend.provider_id}`.",
                )
            provider = slot.provider
            resolve_token = getattr(provider, "_resolve_token", None)
            if not callable(resolve_token):
                raise ImageGenerationError(
                    "image_generation_not_configured",
                    f"Provider '{backend.provider_id}' is not a Codex provider "
                    "(missing OAuth token resolver).",
                )
            return await resolve_token()  # type: ignore[no-any-return]

        return resolver

    def _semaphore_for(
        self,
        provider: str,
        backend: OpenAIImagesBackendConfig
        | MiniMaxBackendConfig
        | CodexImagesBackendConfig,
    ) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(provider)
        if semaphore is None:
            semaphore = asyncio.Semaphore(backend.max_concurrency)
            self._semaphores[provider] = semaphore
        return semaphore

    async def _close_clients(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        self._semaphores.clear()
        await asyncio.gather(
            *(client.close() for client in clients),
            return_exceptions=True,
        )

    def _format_caption(
        self,
        caption: str,
        *,
        prompt: str,
        model: str,
        image: SavedGeneratedImage,
    ) -> str:
        if caption:
            return caption
        template = self._config.caption_template
        if not template:
            return ""
        try:
            return template.format(
                prompt=prompt,
                model=model,
                path=image.relative_path,
                revised_prompt=image.revised_prompt,
            )
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _image_payload(image: SavedGeneratedImage) -> dict[str, Any]:
        return {
            "path": image.relative_path,
            "mime_type": image.mime_type,
            "file_size": image.file_size,
            "revised_prompt": image.revised_prompt,
            "source": image.source,
        }

    @staticmethod
    def _media_payload(image: SavedGeneratedImage) -> dict[str, Any]:
        return {
            "kind": "image",
            "path": image.relative_path,
            "mime_type": image.mime_type,
            "file_size": image.file_size,
            "description": image.revised_prompt,
            "metadata": {
                "source": "image_generation",
                "source_tool": "image_generate",
            },
        }


def _extension_for_mime(mime_type: str, configured_format: str = "") -> str:
    if mime_type == "image/jpeg":
        return "jpg"
    if mime_type == "image/png":
        return "png"
    if mime_type == "image/webp":
        return "webp"
    if mime_type == "image/gif":
        return "gif"
    configured = configured_format.strip().lower().lstrip(".")
    if configured in {"png", "jpg", "jpeg", "webp", "gif"}:
        return "jpg" if configured == "jpeg" else configured
    guessed = mimetypes.guess_extension(mime_type or "")
    return guessed.lstrip(".") if guessed else "png"


def _bounded_image_count(raw_count: int, max_images_per_request: int) -> int:
    return max(1, min(int(raw_count), max_images_per_request))


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes = (remainder + 59) // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{max(1, minutes)}m"


def _configured_command_names(raw_names: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        name = raw_name.strip().lstrip("/!")
        if not name or name in seen:
            continue
        names.append(name)
        seen.add(name)
    return names or ["draw"]


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
