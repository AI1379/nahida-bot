"""Estimate provider-visible prompt tokens without encoding transport data."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nahida_bot.agent.context import ContextMessage, ContextPart
    from nahida_bot.agent.providers.base import ChatProvider, ToolDefinition
    from nahida_bot.agent.tokenization import Tokenizer

MESSAGE_FRAMING_TOKENS = 4
IMAGE_TOKENS = 1024
_TEXT_PART_TYPES = {"text", "image_description"}
_RESPONSES_OUTPUT_TYPES = {"message", "reasoning", "function_call"}


def is_image_part(part: ContextPart) -> bool:
    if part.type == "image_url":
        return bool(part.url)
    return part.type == "image_base64" and bool(part.data and part.mime_type)


def text_parts(parts: list[ContextPart]) -> list[str]:
    return [part.text for part in parts if part.type in _TEXT_PART_TYPES and part.text]


def _wire_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _responses_replay(metadata: dict[str, object]) -> list[dict[str, object]] | None:
    raw = metadata.get("response_output")
    if not isinstance(raw, list):
        return None
    return [
        {key: value for key, value in item.items() if key != "encrypted_content"}
        for item in raw
        if isinstance(item, dict) and item.get("type") in _RESPONSES_OUTPUT_TYPES
    ]


class PromptTokenEstimator:
    """Count text, fixed message framing, and native media independently."""

    def __init__(
        self, tokenizer: Tokenizer, *, api_family: str = "", cache_text: bool = False
    ) -> None:
        self._api_family = api_family
        self._count = tokenizer.count_tokens
        self._count_cached = (
            lru_cache(maxsize=256)(self._count) if cache_text else self._count
        )

    def estimate_tools(
        self, provider: ChatProvider, tools: list[ToolDefinition]
    ) -> int:
        formatted = provider.format_tools(tools)
        return self._count_cached(_wire_json(formatted)) if formatted else 0

    def estimate_message(
        self, message: ContextMessage, *, cache_text: bool = True
    ) -> int:
        count = self._count_cached if cache_text else self._count
        metadata = message.metadata or {}
        if message.role == "assistant" and self._api_family == "openai-responses":
            replay = _responses_replay(metadata)
            if replay is not None:
                return MESSAGE_FRAMING_TOKENS + (
                    count(_wire_json(replay)) if replay else 0
                )

        visible_text = message.content
        image_count = 0
        if message.parts and (
            message.role == "user"
            or self._api_family not in {"anthropic-messages", "openai-responses"}
        ):
            image_count = sum(is_image_part(part) for part in message.parts)
            blocks = text_parts(message.parts)
            if blocks or image_count:
                visible_text = "\n".join(blocks)

        total = MESSAGE_FRAMING_TOKENS + image_count * IMAGE_TOKENS
        if visible_text:
            total += count(visible_text)
        if message.role == "assistant":
            if message.reasoning:
                total += count(message.reasoning)
            tool_calls = metadata.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                total += count(_wire_json(tool_calls))
        elif message.role == "tool":
            call_id = metadata.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                total += count(call_id)
        return total
