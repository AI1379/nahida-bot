"""MotionPlanner: derives a MotionPlan from agent reply text.

V1 uses a remote "cheap" LLM model to analyze the text and assign
emotion/motion/voice tags per sentence. The interface is designed so a
future local ONNX or embedding-based planner can drop in without touching
the call site (``MessageRouter._send_outbound``).

Design notes:
- The planner runs as a post-processor AFTER the main agent LLM produces
  clean text. The main reply stays clean for transcript/memory/channels.
- Failures degrade gracefully: if the planner errors or times out, the
  caller attaches nothing (or a neutral plan) — never blocks the reply.
- Output is a ``MotionPlan`` that serializes to the Desktop DisplayPlan
  wire format via ``to_display_plan_dict()``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

from nahida_bot.agent.motion_plan import MotionPlan

if TYPE_CHECKING:
    from nahida_bot.agent.providers.router import ModelRouter

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
你是角色动画意图分析器。将角色的回复文本按自然句分割，为每句标注情绪、动作和语气。

可选情绪: neutral, happy, thinking, worried, error
可选动作: idle, nod, point, wave, notify, speaking
可选语气: neutral, bright, calm, soft

规则:
- 每个segment的text必须是原文的子串，不要改写或添加内容。
- 短回复（一句话）就输出一个segment。
- emotion反映这句话的情感状态。
- motion是说话时的肢体动作：speaking=普通说话、nod=点头赞同、point=指引强调、wave=打招呼、notify=提醒通知、idle=不说话或停顿。
- voice.style控制语音风格：bright=明亮开心、calm=平静沉稳、soft=温柔轻声、neutral=默认。
- voice.speed范围0.5-1.5，1.0为正常速度。
- voice.pitch范围-6到6（半音），0为正常音高。
- pause_after_ms是这句话说完后的停顿毫秒数，范围0-3000。

输出JSON格式：
{"segments":[{"text":"原文句子","emotion":"happy","motion":"nod","voice":{"style":"bright","speed":1.0,"pitch":0},"pause_after_ms":200}]}

只输出JSON，不要添加任何其他文字、解释或markdown。
# TODO: when multi-voice / persona-bound voice routing is implemented, add
# a `voice` field to the output JSON (e.g. `"voice": "nahida"`) so the Desktop
# can select a specific TTS voice per segment. Currently the gateway always
# uses default_voice."""


@runtime_checkable
class MotionPlanner(Protocol):
    """Analyzes agent reply text and returns a MotionPlan (or None on failure)."""

    async def plan(self, text: str, *, session_id: str = "") -> MotionPlan | None: ...


class NoopMotionPlanner:
    """Always returns None — used when motion planning is disabled."""

    async def plan(self, text: str, *, session_id: str = "") -> MotionPlan | None:
        return None


class LLMMotionPlanner:
    """V1 MotionPlanner: uses a remote "cheap" LLM to analyze reply text.

    The planner resolves ``model_tag`` (e.g. "cheap") through the
    :class:`ModelRouter`, sends a structured prompt, and parses the JSON
    output into a :class:`MotionPlan`. Any failure (timeout, parse error,
    provider error) returns ``None`` so the caller can attach a neutral
    fallback or skip display_plan entirely.
    """

    def __init__(
        self,
        model_router: ModelRouter,
        *,
        model_tag: str = "cheap",
        timeout_seconds: float = 15.0,
    ) -> None:
        self._router = model_router
        self._model_tag = model_tag.strip() or "cheap"
        self._timeout = max(1.0, timeout_seconds)

    async def plan(self, text: str, *, session_id: str = "") -> MotionPlan | None:
        clean = text.strip()
        if not clean:
            return None

        routed = self._router.resolve(self._model_tag)
        if routed is None:
            logger.warning(
                "motion_planner.model_not_found",
                model_tag=self._model_tag,
                session_id=session_id,
            )
            return None

        from nahida_bot.agent.context import ContextMessage

        messages = [
            ContextMessage(role="system", content=_SYSTEM_PROMPT, source=""),
            ContextMessage(role="user", content=clean, source=""),
        ]

        try:
            response = await asyncio.wait_for(
                routed.slot.provider.chat(
                    messages=messages,
                    model=routed.model or routed.slot.default_model,
                ),
                timeout=self._timeout,
            )
        except TimeoutError:
            logger.warning(
                "motion_planner.timeout",
                model_tag=self._model_tag,
                timeout=self._timeout,
                session_id=session_id,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "motion_planner.llm_error",
                error=f"{type(exc).__name__}: {exc}",
                session_id=session_id,
            )
            return None

        raw_content = response.content or ""
        plan = MotionPlan.from_llm_json(raw_content, original_text=clean)
        if plan is None:
            logger.debug(
                "motion_planner.parse_failed",
                response_chars=len(raw_content),
                session_id=session_id,
            )
            # Fallback: neutral plan so Desktop still gets voice + subtitle.
            return MotionPlan.neutral(clean)
        return plan


__all__ = [
    "MotionPlanner",
    "NoopMotionPlanner",
    "LLMMotionPlanner",
]
