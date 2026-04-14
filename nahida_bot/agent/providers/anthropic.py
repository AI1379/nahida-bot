"""Anthropic Claude provider — independent implementation.

The Anthropic Messages API uses a content-block array structure that is
fundamentally different from the OpenAI ``choices[].message`` layout.
This provider is therefore implemented independently and does **not**
inherit from ``OpenAICompatibleProvider``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ProviderResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from nahida_bot.agent.providers.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from nahida_bot.agent.providers.registry import register_provider
from nahida_bot.agent.tokenization import Tokenizer

# Anthropic stop_reason → normalised finish_reason mapping
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "stop_sequence": "stop_sequence",
    "pause_turn": "pause_turn",
    "refusal": "content_filter",
}


@register_provider("anthropic", "Anthropic Claude Provider")
@dataclass(slots=True)
class AnthropicProvider(ChatProvider):
    """Provider for the Anthropic Messages API (``POST /v1/messages``).

    Does **not** inherit ``OpenAICompatibleProvider`` because the Anthropic
    response format uses a content-block array that is structurally
    incompatible with the OpenAI ``choices[].message`` layout.
    """

    base_url: str
    api_key: str
    model: str
    name: str = "anthropic"
    api_family: str = "anthropic-messages"
    max_tokens: int = 4096
    tokenizer_impl: Tokenizer | None = None
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @property
    def tokenizer(self) -> Tokenizer | None:
        return self.tokenizer_impl

    def _ensure_client(self) -> httpx.AsyncClient:
        # TODO: Same lifecycle issue as OpenAICompatibleProvider — see its
        # _ensure_client TODO for details.
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # format_tools: Anthropic uses ``input_schema`` instead of ``parameters``
    # ------------------------------------------------------------------

    def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    # ------------------------------------------------------------------
    # serialize_messages: convert ContextMessage → Anthropic native format
    # ------------------------------------------------------------------

    def _serialize_messages_anthropic(
        self, messages: list[ContextMessage]
    ) -> tuple[str | None, list[dict[str, object]]]:
        """Serialize messages into Anthropic format.

        Returns:
            A tuple of *(system_prompt, messages)* because Anthropic requires
            the system prompt to be passed as a separate top-level parameter,
            not as a message.
        """
        system_prompt: str | None = None
        result: list[dict[str, object]] = []

        for msg in messages:
            if msg.role == "system":
                # Anthropic: system messages are not part of the messages
                # array — they are a separate top-level parameter.
                # We concatenate multiple system messages.
                if system_prompt is None:
                    system_prompt = msg.content
                else:
                    system_prompt += "\n\n" + msg.content
                continue

            if msg.role == "assistant":
                result.append(self._serialize_assistant_message(msg))
            elif msg.role == "tool":
                result.append(self._serialize_tool_result_message(msg))
            else:
                # user messages
                result.append({"role": "user", "content": msg.content})

        return system_prompt, result

    def _serialize_assistant_message(self, msg: ContextMessage) -> dict[str, object]:
        """Serialize an assistant ContextMessage into Anthropic format.

        Injects ``thinking`` / ``redacted_thinking`` blocks when the message
        carries reasoning signatures (required for multi-turn replay).
        """
        blocks: list[dict[str, object]] = []

        # Inject thinking block when a signature is present
        if msg.reasoning_signature:
            blocks.append(
                {
                    "type": "thinking",
                    "thinking": msg.reasoning or "",
                    "signature": msg.reasoning_signature,
                }
            )
            # Inject redacted_thinking block if flagged
            if msg.has_redacted_thinking:
                blocks.append(
                    {
                        "type": "redacted_thinking",
                        "signature": msg.reasoning_signature,
                    }
                )

        # Text block
        if msg.content:
            blocks.append({"type": "text", "text": msg.content})

        # Tool-use blocks from metadata
        if msg.metadata is not None:
            tool_calls_raw = msg.metadata.get("tool_calls")
            if isinstance(tool_calls_raw, list):
                for tc in tool_calls_raw:
                    if not isinstance(tc, dict):
                        continue
                    tc_id = tc.get("id")
                    tc_name = tc.get("name")
                    tc_args = tc.get("arguments", {})
                    if isinstance(tc_id, str) and isinstance(tc_name, str):
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": tc_id,
                                "name": tc_name,
                                "input": tc_args,
                            }
                        )

        if not blocks:
            blocks.append({"type": "text", "text": ""})

        return {"role": "assistant", "content": blocks}

    def _serialize_tool_result_message(self, msg: ContextMessage) -> dict[str, object]:
        """Convert a tool-result ContextMessage to Anthropic ``user/tool_result`` format."""
        tool_call_id = ""
        if msg.metadata is not None:
            raw_id = msg.metadata.get("tool_call_id")
            if isinstance(raw_id, str):
                tool_call_id = raw_id

        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": msg.content,
                }
            ],
        }

    # ------------------------------------------------------------------
    # chat()
    # ------------------------------------------------------------------

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProviderResponse:
        """Call the Anthropic Messages API."""
        system_prompt, serialized_messages = self._serialize_messages_anthropic(
            messages
        )

        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": serialized_messages,
        }
        if system_prompt is not None:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = self.format_tools(tools)

        timeout = timeout_seconds or 60
        endpoint = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        try:
            client = self._ensure_client()
            response = await client.post(
                endpoint, json=payload, headers=headers, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(
                f"HTTP transport error communicating with {self.name}"
            ) from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"Provider auth rejected request with status {response.status_code}"
            )
        if response.status_code == 429:
            raise ProviderRateLimitError()
        if response.status_code >= 500:
            raise ProviderTransportError(
                f"Provider server error: status {response.status_code}"
            )
        if response.status_code >= 400:
            raise ProviderBadResponseError(
                f"Provider rejected request: status {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError("Provider returned non-JSON body") from exc

        return self._parse_response(body)

    # ------------------------------------------------------------------
    # Response parsing — content block iteration
    # ------------------------------------------------------------------

    def _parse_response(self, body: dict[str, object]) -> ProviderResponse:
        """Parse the Anthropic Messages API response body."""
        content_blocks = body.get("content")
        if not isinstance(content_blocks, list):
            raise ProviderBadResponseError("Anthropic response missing content array")

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        reasoning_signature: str | None = None
        has_redacted_thinking = False
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

            elif block_type == "thinking":
                thinking_text = block.get("thinking")
                if isinstance(thinking_text, str) and thinking_text.strip():
                    thinking_parts.append(thinking_text)
                sig = block.get("signature")
                if isinstance(sig, str):
                    reasoning_signature = sig

            elif block_type == "redacted_thinking":
                has_redacted_thinking = True
                sig = block.get("signature")
                if isinstance(sig, str):
                    # Keep the last signature we see for passback
                    reasoning_signature = sig

            elif block_type == "tool_use":
                tc = self._parse_tool_use_block(block)
                if tc is not None:
                    tool_calls.append(tc)

        # Normalise finish reason
        stop_reason_raw = body.get("stop_reason")
        finish_reason: str | None = None
        if isinstance(stop_reason_raw, str):
            finish_reason = _STOP_REASON_MAP.get(stop_reason_raw, stop_reason_raw)

        # Extract refusal
        refusal: str | None = None
        if stop_reason_raw == "refusal":
            refusal = "Anthropic safety policy refusal"

        # Content
        content = "\n".join(text_parts) if text_parts else None
        reasoning_content = "\n".join(thinking_parts) if thinking_parts else None

        # Usage
        usage = self._parse_usage(body)

        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_response=body,
            reasoning_content=reasoning_content,
            reasoning_signature=reasoning_signature,
            has_redacted_thinking=has_redacted_thinking,
            refusal=refusal,
            usage=usage,
        )

    def _parse_tool_use_block(self, block: dict[str, object]) -> ToolCall | None:
        call_id = block.get("id")
        name = block.get("name")
        input_data = block.get("input")

        if not isinstance(call_id, str) or not isinstance(name, str):
            return None

        arguments: dict[str, object] = {}
        if isinstance(input_data, dict):
            arguments = input_data
        elif isinstance(input_data, str):
            try:
                loaded = json.loads(input_data)
                if isinstance(loaded, dict):
                    arguments = loaded
            except json.JSONDecodeError:
                pass

        return ToolCall(call_id=call_id, name=name, arguments=arguments)

    def _parse_usage(self, body: dict[str, object]) -> TokenUsage | None:
        usage_raw = body.get("usage")
        if not isinstance(usage_raw, dict):
            return None

        input_tokens = usage_raw.get("input_tokens", 0)
        output_tokens = usage_raw.get("output_tokens", 0)
        cache_read = usage_raw.get("cache_read_input_tokens", 0)

        return TokenUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
            cached_tokens=cache_read if isinstance(cache_read, int) else 0,
        )
