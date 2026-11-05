"""Tool execution adapter backed by the plugin tool registry."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from nahida_bot.agent.loop import ToolExecutionResult, ToolExecutor
from nahida_bot.agent.providers import ToolCall, ToolDefinition
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry
from nahida_bot.core.exec_runner import ExecCommandError, ExecRunner


def tool_entry_to_definition(entry: ToolEntry) -> ToolDefinition:
    """Convert a registered plugin tool into a provider-facing definition."""
    return ToolDefinition(
        name=entry.name,
        description=entry.description,
        parameters=entry.parameters,
    )


class RegistryToolExecutor(ToolExecutor):
    """Execute provider tool calls through the plugin ToolRegistry."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        exec_runner: ExecRunner | None = None,
        timeout_overrides: dict[str, float] | None = None,
    ) -> None:
        self._registry = registry
        self._exec_runner = exec_runner
        self._timeout_overrides = dict(timeout_overrides or {})
        self._batch_entries: ContextVar[dict[str, ToolEntry] | None] = ContextVar(
            "tool_batch_entries",
            default=None,
        )

    @contextmanager
    def batch_scope(self) -> Iterator[None]:
        token = self._batch_entries.set(
            {entry.name: entry for entry in self._registry.all()}
        )
        try:
            yield
        finally:
            self._batch_entries.reset(token)

    def _entry(self, name: str) -> ToolEntry | None:
        entries = self._batch_entries.get()
        return self._registry.get(name) if entries is None else entries.get(name)

    def timeout_seconds(self, tool_call: ToolCall, default: float) -> float:
        if tool_call.name in self._timeout_overrides:
            return self._timeout_overrides[tool_call.name]
        entry = self._entry(tool_call.name)
        if entry is not None and entry.plugin_id == "builtin-commands":
            if tool_call.name == "exec" and self._exec_runner is not None:
                requested = tool_call.arguments.get("timeout")
                if requested is None or isinstance(requested, (int, float)):
                    return self._exec_runner.tool_budget_seconds(requested)
            if tool_call.name == "agent_wait":
                requested = tool_call.arguments.get("timeout_seconds", 30)
                if isinstance(requested, (int, float)):
                    return max(default, requested + 5)
        return default

    def parallel_safe(self, tool_call: ToolCall) -> bool:
        entry = self._entry(tool_call.name)
        return bool(entry and entry.parallel_safe)

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        """Run a registered plugin tool with the model-provided arguments."""
        entry = self._entry(tool_call.name)
        if entry is None:
            return ToolExecutionResult.error(
                code="tool_not_registered",
                message=f"Tool '{tool_call.name}' is not registered",
                retryable=False,
            )

        try:
            result = await entry.handler(**tool_call.arguments)
        except ExecCommandError as exc:
            return ToolExecutionResult.error(
                code=exc.code,
                message=str(exc),
                retryable=False,
                logs=[f"plugin={entry.plugin_id}"],
            )
        return ToolExecutionResult.success(
            output=result,
            logs=[f"plugin={entry.plugin_id}"],
        )

    def definitions(self) -> list[ToolDefinition]:
        """Return all currently registered tools as provider definitions."""
        return [tool_entry_to_definition(entry) for entry in self._registry.all()]

    def tool_requires_admin(self, tool_name: str) -> bool:
        """Expose registry authorization metadata to the agent loop."""
        entry = self._entry(tool_name)
        return bool(entry and entry.requires_admin)

    def tool_scope(self, tool_name: str) -> str:
        """Expose the registry scope mode to the agent loop."""
        entry = self._entry(tool_name)
        return entry.scope if entry else ""
