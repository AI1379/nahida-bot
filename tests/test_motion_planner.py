"""Tests for LLMMotionPlanner with a mocked LLM provider."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


from nahida_bot.agent.motion_planner import (
    LLMMotionPlanner,
    NoopMotionPlanner,
)


def _mock_router(json_response: str) -> Any:
    response = MagicMock()
    response.content = json_response
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=response)
    router = MagicMock()
    routed = MagicMock()
    routed.slot.provider = provider
    routed.slot.default_model = "cheap-model"
    routed.model = None
    router.resolve.return_value = routed
    return router


VALID_RESPONSE = (
    '{"segments":['
    '{"text":"你好","emotion":"happy","motion":"nod",'
    '"voice":{"style":"bright","speed":1.0,"pitch":0}}'
    "]}"
)

MD_RESPONSE = (
    '```json\n{"segments":[{"text":"你好","emotion":"thinking","motion":"idle"}]}\n```'
)


async def test_llm_planner_returns_plan_on_valid_json() -> None:
    planner = LLMMotionPlanner(_mock_router(VALID_RESPONSE))
    plan = await planner.plan("你好")
    assert plan is not None
    assert plan.segments[0].text == "你好"
    assert plan.segments[0].emotion == "happy"


async def test_llm_planner_handles_markdown_fence() -> None:
    planner = LLMMotionPlanner(_mock_router(MD_RESPONSE))
    plan = await planner.plan("你好")
    assert plan is not None
    assert plan.segments[0].emotion == "thinking"


async def test_llm_planner_returns_neutral_on_parse_failure() -> None:
    planner = LLMMotionPlanner(_mock_router("I'm not JSON"))
    plan = await planner.plan("some text")
    assert plan is not None  # still returns neutral fallback
    assert plan.segments[0].emotion == "neutral"


async def test_llm_planner_returns_none_on_provider_error() -> None:
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=RuntimeError("offline"))
    router = MagicMock()
    routed = MagicMock()
    routed.slot.provider = provider
    routed.slot.default_model = "cheap"
    routed.model = None
    router.resolve.return_value = routed

    planner = LLMMotionPlanner(router)
    plan = await planner.plan("hi")
    assert plan is None


async def test_noop_planner_always_returns_none() -> None:
    planner = NoopMotionPlanner()
    assert await planner.plan("anything") is None
    assert await planner.plan("") is None


async def test_llm_planner_returns_none_for_empty_text() -> None:
    planner = LLMMotionPlanner(_mock_router(VALID_RESPONSE))
    assert await planner.plan("") is None
    assert await planner.plan("   ") is None
