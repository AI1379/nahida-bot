"""Tool execution adapter backed by the plugin tool registry."""

from __future__ import annotations

from nahida_bot.agent.loop import ToolExecutionResult, ToolExecutor
from nahida_bot.agent.providers import ToolCall, ToolDefinition
from nahida_bot.plugins.registry import ToolEntry, ToolRegistry


def tool_entry_to_definition(entry: ToolEntry) -> ToolDefinition:
    """Convert a registered plugin tool into a provider-facing definition."""
    return ToolDefinition(
        name=entry.name,
        description=entry.description,
        parameters=entry.parameters,
    )


class RegistryToolExecutor(ToolExecutor):
    """Execute provider tool calls through the plugin ToolRegistry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_call: ToolCall) -> ToolExecutionResult:
        """Run a registered plugin tool with the model-provided arguments."""
        entry = self._registry.get(tool_call.name)
        if entry is None:
            return ToolExecutionResult.error(
                code="tool_not_registered",
                message=f"Tool '{tool_call.name}' is not registered",
                retryable=False,
            )

        result = await entry.handler(**tool_call.arguments)
        return ToolExecutionResult.success(
            output=result,
            logs=[f"plugin={entry.plugin_id}"],
        )

    def definitions(self) -> list[ToolDefinition]:
        """Return all currently registered tools as provider definitions."""
        return [tool_entry_to_definition(entry) for entry in self._registry.all()]
