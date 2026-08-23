"""WebAPI text generation endpoint: persona-grounded one-shot generation.

``POST /api/generate`` runs a single chat completion for clients (currently
the Desktop) with a server-owned persona prefix: the configured system
prompt baseline plus the target workspace's instruction files
(``AGENTS.md`` → ``SOUL.md`` → ``USER.md``) — the same messages the agent
pipeline injects for conversations, so generated lines speak with the same
voice as chat replies. The client owns the task instruction (``prompt``)
and generation parameters; guardrails (output cleaning, character clamps,
avoid-list retries, model routing) stay server-side.

The model resolution order is the request's ``model`` spec (a tag such as
``"primary"``/``"cheap"`` or a concrete ``provider/model``), then the
gateway-side ``webapi.generate.model`` config, then the ``primary`` tag,
then the default provider.

When ``synthesize`` is true and ``webapi.speech`` is enabled the returned
text is pre-synthesized so trigger-time playback hits the speech-job cache
instead of waiting for real-time synthesis.

Route reuses the WebUI admin auth dependency (``require_token``), same as
the speech routes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nahida_bot.agent.context import ContextBuilder, ContextMessage, ContextPart
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.routes.speech import synthesize_speech_job
from nahida_bot.workspace.exceptions import (
    WorkspaceError,
    WorkspaceNotFoundError,
    WorkspaceValidationError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/generate", tags=["generate"])

MAX_PROMPT_CHARS = 4000
MAX_OUTPUT_CHARS = 200
MAX_AVOID_ITEMS = 12
MAX_AVOID_CHARS = 200
MAX_MODEL_CHARS = 128
MAX_STYLE_CHARS = 64
# Ceiling on the injected persona files combined; guards against runaway
# instruction files bloating every generation call.
MAX_PERSONA_CHARS = 48_000
GENERATION_ATTEMPTS = 2


class GenerateTextRequest(BaseModel):
    """Body of ``POST /api/generate``."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=MAX_PROMPT_CHARS,
        description=(
            "Task instruction. Persona is injected server-side from the "
            "workspace instruction files; do not include persona text here."
        ),
    )
    max_chars: int = Field(
        default=80,
        ge=1,
        le=MAX_OUTPUT_CHARS,
        description="Output clamp applied after cleaning.",
    )
    avoid: list[str] = Field(
        default_factory=list,
        description="Recently used lines the model must not repeat.",
    )
    model: str = Field(
        default="",
        max_length=MAX_MODEL_CHARS,
        description=(
            "Model spec (tag like 'primary'/'cheap' or 'provider/model'); "
            "empty = the webapi.generate.model config, then the primary "
            "tag, then the default provider."
        ),
    )
    workspace: str = Field(
        default="",
        max_length=64,
        description=(
            "Workspace id whose instruction files provide the persona; "
            "empty = the active workspace."
        ),
    )
    synthesize: bool = Field(
        default=True,
        description="Also pre-synthesize speech so trigger-time playback is instant.",
    )
    style: str = Field(
        default="neutral",
        max_length=MAX_STYLE_CHARS,
        description=(
            "TTS style for pre-synthesis; must match the style the client "
            "will request at playback time or the cache key never hits."
        ),
    )


class GenerateTextResponse(BaseModel):
    """Response for a successful generation."""

    text: str
    speech: dict[str, Any] | None = Field(
        default=None,
        description="Speech job artifact when synthesis succeeded; null otherwise.",
    )


def _clean_generated(raw: str, max_chars: int) -> str:
    text = raw.strip()
    # Models love wrapping the line in quotes despite the prompt.
    quotes = "\"'“”‘’「」"
    while text and text[0] in quotes:
        text = text[1:].strip()
    while text and text[-1] in quotes:
        text = text[:-1].strip()
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line[:max_chars]


def _persona_messages(
    application: Any,
    workspace_id: str,
) -> list[ContextMessage]:
    """System baseline + workspace instruction files, chat-pipeline style."""
    messages: list[ContextMessage] = []
    settings = getattr(application, "settings", None)
    baseline = (getattr(settings, "system_prompt", "") or "").strip()
    if baseline:
        messages.append(
            ContextMessage(
                role="system",
                source="system_baseline",
                content=baseline,
            )
        )

    manager = getattr(application, "workspace_manager", None)
    if manager is None or settings is None or not workspace_id:
        return messages

    root = manager.workspace_path(workspace_id)
    builder = ContextBuilder()
    total = 0
    for message in builder.load_workspace_instructions(Path(root)):
        if total + len(message.content) > MAX_PERSONA_CHARS:
            logger.warning(
                "webapi.generate.persona_truncated",
                workspace=workspace_id,
                limit=MAX_PERSONA_CHARS,
            )
            break
        messages.append(message)
        total += len(message.content)
    return messages


def _build_user_message(prompt: str, avoid: list[str]) -> ContextMessage:
    lines = [prompt.strip()]
    if avoid:
        lines.append("避免重复以下已经用过的句子：")
        lines.extend(f"- {item}" for item in avoid)
    content = "\n".join(lines)
    return ContextMessage(
        role="user",
        source="webapi.generate",
        content=content,
        parts=[ContextPart(type="text", text=content)],
    )


@router.post("/text", response_model=GenerateTextResponse)
async def generate_text(
    body: GenerateTextRequest,
    request: Request,
    app=Depends(get_application),
) -> GenerateTextResponse:
    """Generate one cleaned text line with the workspace persona prefix."""
    application = getattr(request.app.state, "application", None)
    model_router = getattr(application, "model_router", None)

    avoid = [item.strip() for item in body.avoid if item.strip()]
    if len(avoid) > MAX_AVOID_ITEMS or any(
        len(item) > MAX_AVOID_CHARS for item in avoid
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"avoid must hold at most {MAX_AVOID_ITEMS} items of at most "
                f"{MAX_AVOID_CHARS} characters."
            ),
        )

    workspace_manager = getattr(application, "workspace_manager", None)
    if body.workspace.strip():
        workspace_id = body.workspace.strip()
        if workspace_manager is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown workspace: {workspace_id}",
            )
        try:
            # get_sandbox (unlike workspace_path) also checks the workspace
            # exists in the index, not just that the id is well-formed.
            workspace_manager.get_sandbox(workspace_id)
        except (WorkspaceNotFoundError, WorkspaceValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown workspace: {workspace_id}",
            ) from exc
    else:
        workspace_id = ""
        if workspace_manager is not None:
            try:
                workspace_id = workspace_manager.get_active_workspace().workspace_id
            except WorkspaceError:
                logger.warning(
                    "webapi.generate.active_workspace_unavailable",
                )

    generate_cfg = getattr(request.app.state, "generate_config", None)
    # The client decides which model generates; its request spec wins, the
    # gateway-side webapi.generate.model is the server default.
    model_spec = (body.model or getattr(generate_cfg, "model", "") or "").strip()
    routed = (
        model_router.resolve_for_task(
            "text_generate",
            explicit=model_spec,
            default_spec="primary",
            fallback="default",
        )
        if model_router is not None
        else None
    )
    if routed is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No chat model is configured for text generation "
                "(set the request model, webapi.generate.model, or tag a "
                "model as primary)."
            ),
        )

    messages = [*_persona_messages(application, workspace_id)]
    messages.append(_build_user_message(body.prompt, avoid))

    text = ""
    for _ in range(GENERATION_ATTEMPTS):
        try:
            response = await routed.slot.provider.chat(
                messages=list(messages),
                model=routed.model or routed.slot.default_model,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            logger.warning(
                "webapi.generate.generation_failed",
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Text generation failed: {type(exc).__name__}",
            ) from exc
        candidate = _clean_generated(response.content or "", body.max_chars)
        if not candidate:
            continue
        if candidate in avoid:
            continue
        text = candidate
        break
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Text generation returned no usable text.",
        )

    speech: dict[str, Any] | None = None
    if body.synthesize:
        service = getattr(request.app.state, "speech_service", None)
        store = getattr(request.app.state, "speech_artifact_store", None)
        config = getattr(request.app.state, "speech_config", None)
        if service is not None and store is not None and config is not None:
            try:
                job = await synthesize_speech_job(
                    service,
                    store,
                    config,
                    text=text,
                    style=body.style,
                )
                speech = job.model_dump()
            except HTTPException as exc:
                # Text-only delivery is still useful (clients fall back to
                # system speech), so a TTS warm-up failure is not fatal.
                logger.warning(
                    "webapi.generate.tts_warmup_failed",
                    status_code=exc.status_code,
                )

    return GenerateTextResponse(text=text, speech=speech)


__all__ = ["router"]
