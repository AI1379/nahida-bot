"""Live end-to-end integration test: workspace -> provider -> tool -> reply.

Validates the complete closed-loop required by Phase 2.6 acceptance:
workspace instruction loading → provider call → tool call → final reply,
with metrics tracing and memory persistence.

Skipped unless live provider env vars are configured.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nahida_bot.agent.context import ContextBudget, ContextBuilder
from nahida_bot.agent.loop import (
    AgentLoop,
    AgentLoopConfig,
    ToolExecutionResult,
    ToolExecutor,
)
from nahida_bot.agent.memory import ConversationTurn, SQLiteMemoryStore
from nahida_bot.agent.metrics import MetricsCollector
from nahida_bot.agent.providers import (
    OpenAICompatibleProvider,
    ToolCall,
    ToolDefinition,
)
from nahida_bot.db.engine import DatabaseEngine


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _LiveEchoToolExecutor(ToolExecutor):
    """Executor that echoes back the tool arguments as the output."""

    calls: list[ToolCall] = field(default_factory=list)

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        self.calls.append(tool_call)
        return ToolExecutionResult.success(
            output={"echo": tool_call.arguments},
            logs=[f"executed {tool_call.name}"],
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_with_instructions(tmp_path: Path) -> Path:
    """Create a temporary workspace with an AGENTS.md instruction file."""
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(
        "You are a helpful test assistant for integration testing.",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
async def memory_store() -> AsyncGenerator[SQLiteMemoryStore, None]:
    engine = DatabaseEngine(":memory:")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    await store.ensure_session("e2e-live-session")
    yield store
    await engine.close()


# ---------------------------------------------------------------------------
# E2E live test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.network
@pytest.mark.slow
@pytest.mark.live
@pytest.mark.asyncio
async def test_e2e_live_closed_loop(
    live_llm_config: dict[str, str] | None,
    workspace_with_instructions: Path,
    memory_store: SQLiteMemoryStore,
) -> None:
    """Full closed loop with real backend: workspace → provider → tool → reply → metrics → memory."""
    if live_llm_config is None:
        pytest.skip("Live LLM config is not provided")

    # 1. Real provider.
    provider = OpenAICompatibleProvider(
        base_url=live_llm_config["base_url"],
        api_key=live_llm_config["api_key"],
        model=live_llm_config["model"],
    )

    # 2. Tool executor.
    tool_exec = _LiveEchoToolExecutor()

    # 3. Metrics collector.
    metrics = MetricsCollector()

    # 4. Context builder.
    context_builder = ContextBuilder(
        budget=ContextBudget(max_tokens=4096, reserved_tokens=512),
        provider=provider,
    )

    # 5. Agent loop.
    loop = AgentLoop(
        provider=provider,
        context_builder=context_builder,
        tool_executor=tool_exec,
        metrics=metrics,
        config=AgentLoopConfig(
            max_steps=5,
            provider_timeout_seconds=60,
        ),
    )

    # 6. Run.
    result = await loop.run(
        user_message=(
            'Please call the `read_file` tool with argument {"path":"test.txt"}. '
            "After you receive the tool result, reply with a short sentence that contains DONE."
        ),
        system_prompt="You are a strict agent. Always call the tool when asked.",
        workspace_root=workspace_with_instructions,
        tools=[
            ToolDefinition(
                name="read_file",
                description="Read a file from the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            )
        ],
    )

    # --- Assertions -------------------------------------------------------

    # Final response was produced.
    assert result.final_response
    assert result.error is None

    # Trace ID is present (Phase 2.6 trace linkage).
    assert result.trace_id is not None

    # Tool was executed at least once.
    assert len(tool_exec.calls) >= 1
    assert tool_exec.calls[0].name == "read_file"

    # Metrics were recorded.
    assert metrics.trace_count == 1
    assert metrics.provider_latency_stats()["count"] >= 1.0
    assert metrics.tool_success_rate() == 1.0

    # 7. Persist conversation to memory store.
    await memory_store.append_turn(
        "e2e-live-session",
        ConversationTurn(role="user", content="Read test.txt for me"),
    )
    for msg in result.assistant_messages:
        await memory_store.append_turn(
            "e2e-live-session",
            ConversationTurn(role="assistant", content=msg.content),
        )

    # Memory retrieval works.
    recent = await memory_store.get_recent("e2e-live-session")
    assert len(recent) >= 2
    assert recent[0].turn.role == "user"

    # Keyword search works.
    results = await memory_store.search("e2e-live-session", "test")
    assert len(results) >= 1
