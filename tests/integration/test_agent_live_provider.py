"""Live integration test for OpenAI-compatible backend.

This test is optional and skipped unless live provider env vars are configured.
"""

from __future__ import annotations

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers import OpenAICompatibleProvider


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_openai_compatible_roundtrip(live_llm_config):
    """Validate one real roundtrip against configured OpenAI-compatible endpoint."""
    if live_llm_config is None:
        pytest.skip("Live LLM config is not provided")

    provider = OpenAICompatibleProvider(
        base_url=live_llm_config["base_url"],
        api_key=live_llm_config["api_key"],
        model=live_llm_config["model"],
    )
    context_builder = ContextBuilder(
        budget=ContextBudget(max_tokens=4096, reserved_tokens=1024),
        provider=provider,
    )
    loop = AgentLoop(provider=provider, context_builder=context_builder)

    result = await loop.run(
        user_message="Reply with exactly: PONG",
        system_prompt="You are a concise assistant.",
    )

    assert result.final_response
