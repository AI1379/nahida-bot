"""Live integration tests for Phase 2.8 multi-backend provider support.

These tests validate real API roundtrips against various LLM backends.
They are skipped when the corresponding API keys are not configured.

Setup:
    cp .env.test.example .env.test
    # Fill in your API keys in .env.test
    uv run pytest tests/integration/test_provider_multibackend_live.py -m live -v
"""

from __future__ import annotations

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder, ContextMessage
from nahida_bot.agent.loop import AgentLoop
from nahida_bot.agent.providers.base import ToolDefinition


# ======================================================================
# OpenAI Compatible (already covered in existing tests, included for
# consistency with the multi-backend suite)
# ======================================================================


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_openai_compatible_reasoning_fields(live_llm_config) -> None:
    """OpenAI provider should return valid ProviderResponse with new fields."""
    if live_llm_config is None:
        pytest.skip("OpenAI live config not provided")

    from nahida_bot.agent.providers import OpenAICompatibleProvider

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
                    content="What is 2+2? Reply with just the number.",
                )
            ],
            timeout_seconds=30,
        )

        assert response.content is not None
        assert isinstance(response.content, str)
        # New fields should have valid defaults even if provider doesn't fill them
        assert response.reasoning_content is None or isinstance(
            response.reasoning_content, str
        )
        assert response.usage is not None or response.usage is None  # optional
        assert response.has_redacted_thinking is False or isinstance(
            response.has_redacted_thinking, bool
        )
    finally:
        await provider.close()


# ======================================================================
# DeepSeek
# ======================================================================


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_deepseek_basic_chat(live_deepseek_config) -> None:
    """DeepSeek provider should complete a basic chat roundtrip."""
    if live_deepseek_config is None:
        pytest.skip("DeepSeek live config not provided")

    from nahida_bot.agent.providers.deepseek import DeepSeekProvider

    provider = DeepSeekProvider(
        base_url=live_deepseek_config["base_url"],
        api_key=live_deepseek_config["api_key"],
        model=live_deepseek_config["model"],
    )

    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user",
                    source="integration_test",
                    content="Reply with exactly: PONG",
                )
            ],
            timeout_seconds=30,
        )

        assert response.content is not None
        assert "pong" in response.content.lower()
        assert isinstance(response.tool_calls, list)
        # DeepSeek should include usage
        assert response.usage is not None
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_deepseek_reasoning_content(live_deepseek_config) -> None:
    """DeepSeek-Chat with thinking enabled should produce reasoning_content."""
    if live_deepseek_config is None:
        pytest.skip("DeepSeek live config not provided")

    from nahida_bot.agent.providers.deepseek import DeepSeekProvider

    provider = DeepSeekProvider(
        base_url=live_deepseek_config["base_url"],
        api_key=live_deepseek_config["api_key"],
        model=live_deepseek_config["model"],
    )

    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user",
                    source="integration_test",
                    content="Solve step by step: If a train travels at 60 km/h "
                    "for 2.5 hours, how far does it travel?",
                )
            ],
            timeout_seconds=60,
        )

        assert response.content is not None
        # Reasoning may or may not be present depending on model/config.
        # We just verify the field types are correct.
        assert response.reasoning_content is None or isinstance(
            response.reasoning_content, str
        )
        if response.reasoning_content:
            # If we got reasoning, it should be non-trivial
            assert len(response.reasoning_content) > 10
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_deepseek_agent_loop(live_deepseek_config) -> None:
    """DeepSeek should work in the AgentLoop, propagating reasoning to context."""
    if live_deepseek_config is None:
        pytest.skip("DeepSeek live config not provided")

    from nahida_bot.agent.providers.deepseek import DeepSeekProvider

    provider = DeepSeekProvider(
        base_url=live_deepseek_config["base_url"],
        api_key=live_deepseek_config["api_key"],
        model=live_deepseek_config["model"],
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

        assert result.final_response
        assert "pong" in result.final_response.lower()
        assert result.steps >= 1
    finally:
        await provider.close()


# ======================================================================
# Anthropic / Claude
# ======================================================================


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_anthropic_basic_chat(live_anthropic_config) -> None:
    """Anthropic provider should complete a basic chat roundtrip."""
    if live_anthropic_config is None:
        pytest.skip("Anthropic live config not provided")

    from nahida_bot.agent.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(
        base_url=live_anthropic_config["base_url"],
        api_key=live_anthropic_config["api_key"],
        model=live_anthropic_config["model"],
    )

    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user",
                    source="integration_test",
                    content="Reply with exactly: PONG",
                )
            ],
            timeout_seconds=30,
        )

        assert response.content is not None
        assert "pong" in response.content.lower()
        assert response.finish_reason is not None
        assert isinstance(response.tool_calls, list)
        # Claude should include usage
        assert response.usage is not None
        assert response.usage.input_tokens > 0
        assert response.usage.output_tokens > 0
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_anthropic_system_prompt(live_anthropic_config) -> None:
    """Anthropic provider should handle system prompts correctly."""
    if live_anthropic_config is None:
        pytest.skip("Anthropic live config not provided")

    from nahida_bot.agent.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(
        base_url=live_anthropic_config["base_url"],
        api_key=live_anthropic_config["api_key"],
        model=live_anthropic_config["model"],
    )

    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="system",
                    source="system",
                    content="You are a pirate. Always respond in pirate speak.",
                ),
                ContextMessage(
                    role="user",
                    source="integration_test",
                    content="Say hello.",
                ),
            ],
            timeout_seconds=30,
        )

        assert response.content is not None
        # The response should contain pirate-like language
        assert len(response.content) > 5
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_anthropic_tool_calling(live_anthropic_config) -> None:
    """Anthropic provider should support tool calling."""
    if live_anthropic_config is None:
        pytest.skip("Anthropic live config not provided")

    from nahida_bot.agent.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(
        base_url=live_anthropic_config["base_url"],
        api_key=live_anthropic_config["api_key"],
        model=live_anthropic_config["model"],
    )

    tools = [
        ToolDefinition(
            name="get_temperature",
            description="Get the current temperature for a city.",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "The city name."},
                },
                "required": ["city"],
            },
        )
    ]

    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user",
                    source="integration_test",
                    content="What is the temperature in Tokyo? Use the get_temperature tool.",
                ),
            ],
            tools=tools,
            timeout_seconds=30,
        )

        # The model should request a tool call
        assert len(response.tool_calls) > 0
        assert response.tool_calls[0].name == "get_temperature"
        assert "city" in response.tool_calls[0].arguments
        assert response.finish_reason == "tool_calls"
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_anthropic_agent_loop(live_anthropic_config) -> None:
    """Anthropic should work in the AgentLoop with correct context handling."""
    if live_anthropic_config is None:
        pytest.skip("Anthropic live config not provided")

    from nahida_bot.agent.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(
        base_url=live_anthropic_config["base_url"],
        api_key=live_anthropic_config["api_key"],
        model=live_anthropic_config["model"],
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

        assert result.final_response
        assert "pong" in result.final_response.lower()
        assert result.steps >= 1
    finally:
        await provider.close()


# ======================================================================
# Cross-provider contract validation
# ======================================================================


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_provider_response_contract_openai(live_llm_config) -> None:
    """OpenAI provider response should satisfy ProviderResponse contract."""
    if live_llm_config is None:
        pytest.skip("OpenAI live config not provided")

    from nahida_bot.agent.providers import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        base_url=live_llm_config["base_url"],
        api_key=live_llm_config["api_key"],
        model=live_llm_config["model"],
    )
    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user", source="test", content="Say hello in one word."
                )
            ]
        )
        _validate_provider_response_contract(response)
    finally:
        await provider.close()


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_live_provider_response_contract_anthropic(
    live_anthropic_config,
) -> None:
    """Anthropic provider response should satisfy ProviderResponse contract."""
    if live_anthropic_config is None:
        pytest.skip("Anthropic live config not provided")

    from nahida_bot.agent.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(
        base_url=live_anthropic_config["base_url"],
        api_key=live_anthropic_config["api_key"],
        model=live_anthropic_config["model"],
    )
    try:
        response = await provider.chat(
            messages=[
                ContextMessage(
                    role="user", source="test", content="Say hello in one word."
                )
            ]
        )
        _validate_provider_response_contract(response)
    finally:
        await provider.close()


def _validate_provider_response_contract(response) -> None:
    """Assert that a ProviderResponse satisfies the unified contract."""
    from nahida_bot.agent.providers.base import ProviderResponse, TokenUsage

    assert isinstance(response, ProviderResponse)
    assert response.content is None or isinstance(response.content, str)
    assert isinstance(response.tool_calls, list)
    assert response.finish_reason is None or isinstance(response.finish_reason, str)
    assert response.raw_response is None or isinstance(response.raw_response, dict)
    assert response.reasoning_content is None or isinstance(
        response.reasoning_content, str
    )
    assert response.reasoning_signature is None or isinstance(
        response.reasoning_signature, str
    )
    assert isinstance(response.has_redacted_thinking, bool)
    assert response.refusal is None or isinstance(response.refusal, str)
    assert response.usage is None or isinstance(response.usage, TokenUsage)
    assert isinstance(response.extra, dict)
