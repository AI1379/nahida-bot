"""DeepSeek provider — OpenAI-compatible with thinking mode support."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.openai_compatible import OpenAICompatibleProvider
from nahida_bot.agent.providers.registry import register_provider


@register_provider("deepseek", "DeepSeek Provider")
@dataclass(slots=True)
class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek Provider with thinking mode support.

    DeepSeek V4 (``deepseek-v4-pro``) and R1 models support a thinking mode
    where the model outputs chain-of-thought reasoning (``reasoning_content``)
    before the final answer.

    **Critical**: for tool-call turns, ``reasoning_content`` MUST be replayed
    in ALL subsequent requests. Omitting it causes a 400 error from the API.
    See https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
    """

    name: str = "deepseek"

    thinking_enabled: bool = True

    # Reasoning effort — reserved interface for future chat-command integration.
    # Valid values: "high", "max". None = provider default (high for normal,
    # max for agent-style requests like Claude Code).
    reasoning_effort: str | None = None

    def _extra_payload(self) -> dict[str, object]:
        params: dict[str, object] = {}
        if self.thinking_enabled:
            params["thinking"] = {"type": "enabled"}
        if self.reasoning_effort is not None:
            params["reasoning_effort"] = self.reasoning_effort
        return params

    def _serialize_message(self, message: ContextMessage) -> dict[str, object]:
        payload = OpenAICompatibleProvider._serialize_message(self, message)

        # DeepSeek requires reasoning_content in ALL assistant messages that
        # have tool_calls, even when the reasoning string is empty.  The parent
        # class only injects it when message.reasoning is truthy, so we patch
        # the missing case here.
        if (
            message.role == "assistant"
            and message.metadata is not None
            and message.metadata.get("tool_calls")
            and self.reasoning_key not in payload
        ):
            payload[self.reasoning_key] = message.reasoning or ""

        return payload
