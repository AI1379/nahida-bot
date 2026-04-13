"""Live integration test for OpenAI-compatible backend.

This test is optional and skipped unless live provider env vars are configured.
"""

from __future__ import annotations

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.context import ContextMessage
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

    try:
        result = await loop.run(
            user_message="Reply with exactly: PONG",
            system_prompt="You are a concise assistant.",
        )

        # Phase 2.8: The model may put the answer inside reasoning content
        # (think tags), leaving final_response empty. Check both locations.
        response_text = result.final_response
        if not response_text and result.assistant_messages:
            last_msg = result.assistant_messages[-1]
            if last_msg.reasoning:
                response_text = last_msg.reasoning
        assert response_text, "Expected either content or reasoning from provider"
        assert "pong" in response_text.lower()
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_openai_compatible_provider_contract(live_llm_config):
    """Validate direct provider contract on a real backend response."""
    if live_llm_config is None:
        pytest.skip("Live LLM config is not provided")

    provider = OpenAICompatibleProvider(
        base_url=live_llm_config["base_url"],
        api_key=live_llm_config["api_key"],
        model=live_llm_config["model"],
    )

    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user",
                    source="integration_test",
                    content="Reply with one short greeting.",
                )
            ],
            timeout_seconds=30,
        )

        assert response.content is None or isinstance(response.content, str)
        assert isinstance(response.tool_calls, list)
        assert response.raw_response is not None
    finally:
        await provider.close()
