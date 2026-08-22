"""WebAPI pomodoro endpoint: dynamic reminder text + TTS cache warm-up.

``POST /api/pomodoro/reminders`` generates one short reminder line with the
task-bound chat model (``pomodoro_reminder``) and, when ``synthesize`` is
true and ``webapi.speech`` is enabled, pre-synthesizes the audio so the
Desktop hits the speech-job cache at trigger time instead of waiting for
real-time synthesis. The Desktop calls this during the pomodoro phase
runway; on any failure it falls back to its static reminder texts.

Route reuses the WebUI admin auth dependency (``require_token``), same as
the speech routes.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nahida_bot.agent.context import ContextMessage, ContextPart
from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.routes.speech import synthesize_speech_job

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/pomodoro", tags=["pomodoro"])

POMODORO_PHASES = ("work_start", "break_start", "break_end", "rounds_done")

MAX_AVOID_ITEMS = 12
MAX_AVOID_CHARS = 200
MAX_GENERATED_CHARS = 80
GENERATION_ATTEMPTS = 2

# Style/voice/speed/pitch must mirror what the Desktop reminder segments send
# to /api/speech/jobs at playback time, otherwise the pre-warmed cache key
# never matches and every trigger re-synthesizes.
_DESKTOP_REMINDER_STYLE = "neutral"

_PHASE_INSTRUCTIONS = {
    "work_start": "番茄钟专注时段刚刚开始",
    "break_start": "专注时段结束、休息时段刚刚开始",
    "break_end": "休息时段结束、马上要开始下一轮专注",
    "rounds_done": "所有番茄钟轮次刚刚全部完成",
}


class PomodoroReminderRequest(BaseModel):
    """Body of ``POST /api/pomodoro/reminders``."""

    phase: str = Field(..., description="work_start | break_start | break_end")
    avoid: list[str] = Field(
        default_factory=list,
        description="Recently used reminder lines the model must not repeat.",
    )
    synthesize: bool = Field(
        default=True,
        description="Also pre-synthesize speech so trigger-time playback is instant.",
    )


class PomodoroReminderResponse(BaseModel):
    """Response for a successful generation."""

    phase: str
    text: str
    speech: dict[str, Any] | None = Field(
        default=None,
        description="Speech job artifact when synthesis succeeded; null otherwise.",
    )


def _build_prompt(phase: str, avoid: list[str]) -> str:
    lines = [
        "你是用户的桌面精灵纳西妲，性格温柔、活泼、爱操心但很会鼓励人。",
        f"场景：{_PHASE_INSTRUCTIONS[phase]}。",
        "请生成一句说给用户听的提醒语。要求：",
        "中文；不超过 40 个字；口语化、亲切自然，符合纳西妲的语气；",
        "不要使用引号、emoji 或颜文字；不要以“纳西妲”开头；",
        "直接输出这一句话本身，不要任何解释或前缀。",
    ]
    if avoid:
        lines.append("避免重复以下已经用过的句子：")
        lines.extend(f"- {item}" for item in avoid)
    return "\n".join(lines)


def _clean_generated(raw: str) -> str:
    text = raw.strip()
    # Models love wrapping the line in quotes despite the prompt.
    quotes = "\"'“”‘’「」"
    while text and text[0] in quotes:
        text = text[1:].strip()
    while text and text[-1] in quotes:
        text = text[:-1].strip()
    first_line = text.splitlines()[0].strip() if text else ""
    return first_line[:MAX_GENERATED_CHARS]


@router.post("/reminders", response_model=PomodoroReminderResponse)
async def generate_pomodoro_reminder(
    body: PomodoroReminderRequest,
    request: Request,
    app=Depends(get_application),
) -> PomodoroReminderResponse:
    """Generate one dynamic reminder line, optionally pre-synthesizing TTS."""
    if body.phase not in POMODORO_PHASES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="phase must be work_start, break_start, or break_end.",
        )
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

    application = getattr(request.app.state, "application", None)
    model_router = getattr(application, "model_router", None)
    routed = (
        model_router.resolve_for_task(
            "pomodoro_reminder",
            default_spec="chat",
            fallback="disabled",
        )
        if model_router is not None
        else None
    )
    if routed is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No chat model is configured for pomodoro reminders.",
        )

    prompt = _build_prompt(body.phase, avoid)
    message = ContextMessage(
        role="user",
        source="pomodoro_reminder",
        content=prompt,
        parts=[ContextPart(type="text", text=prompt)],
    )

    text = ""
    for _ in range(GENERATION_ATTEMPTS):
        try:
            response = await routed.slot.provider.chat(
                messages=[message],
                model=routed.model or routed.slot.default_model,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            logger.warning(
                "pomodoro_reminder.generation_failed",
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Reminder generation failed: {type(exc).__name__}",
            ) from exc
        candidate = _clean_generated(response.content or "")
        if not candidate:
            continue
        if candidate in avoid:
            continue
        text = candidate
        break
    if not text:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Reminder generation returned no usable text.",
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
                    style=_DESKTOP_REMINDER_STYLE,
                )
                speech = job.model_dump()
            except HTTPException as exc:
                # Text-only delivery is still useful (Desktop falls back to
                # system speech), so a TTS warm-up failure is not fatal.
                logger.warning(
                    "pomodoro_reminder.tts_warmup_failed",
                    status_code=exc.status_code,
                )

    return PomodoroReminderResponse(phase=body.phase, text=text, speech=speech)


__all__ = ["router"]
