"""OpenAI-compatible provider implementation."""

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
from nahida_bot.agent.providers.reasoning import _ReasoningMixin
from nahida_bot.agent.providers.registry import register_provider
from nahida_bot.agent.tokenization import Tokenizer


@register_provider("openai-compatible", "OpenAI-compatible Provider")
@dataclass(slots=True)
class OpenAICompatibleProvider(_ReasoningMixin, ChatProvider):
    """Provider for OpenAI-compatible ``/chat/completions`` endpoints.

    Subclasses for specific backends (DeepSeek, GLM, Groq, Minimax) only need
    to override ``reasoning_key`` and/or ``serialize_messages`` as needed.
    """

    base_url: str
    api_key: str
    model: str
    name: str = "openai-compatible"
    api_family: str = "openai-completions"
    tokenizer_impl: Tokenizer | None = None
    reasoning_key: str = "reasoning_content"
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @property
    def tokenizer(self) -> Tokenizer | None:
        """Expose provider tokenizer to context budgeting."""
        return self.tokenizer_impl

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it if needed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProviderResponse:
        """Call OpenAI-compatible chat completion API."""
        payload: dict[str, object] = {
            "model": self.model,
            "messages": self.serialize_messages(messages),
        }
        if tools:
            payload["tools"] = self.format_tools(tools)

        timeout = timeout_seconds or 30
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
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

        choice = self._extract_first_choice(body)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderBadResponseError(
                "Missing message payload in provider response"
            )

        # --- Extract content ---
        content = message.get("content")
        normalized_content = content if isinstance(content, str) else None

        # --- Extract reasoning (Phase 2.8) ---
        reasoning_content, cleaned_content = self._extract_reasoning_from_message(
            message
        )
        # If think tags were found inside content, use cleaned version
        if cleaned_content is not None:
            normalized_content = cleaned_content

        # --- Extract finish_reason ---
        finish_reason_raw = choice.get("finish_reason")
        finish_reason = (
            finish_reason_raw if isinstance(finish_reason_raw, str) else None
        )

        # --- Extract refusal (OpenAI) ---
        refusal_raw = message.get("refusal")
        refusal = refusal_raw if isinstance(refusal_raw, str) else None

        # --- Extract tool calls ---
        tool_calls_payload = message.get("tool_calls")
        tool_calls = self._parse_tool_calls(tool_calls_payload)

        # --- Extract usage statistics ---
        usage = self._parse_usage(body)

        return ProviderResponse(
            content=normalized_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_response=body,
            reasoning_content=reasoning_content,
            refusal=refusal,
            usage=usage,
        )

    # -- Serialization --

    def serialize_messages(
        self, messages: list[ContextMessage]
    ) -> list[dict[str, object]]:
        """Serialize context messages to OpenAI-compatible format.

        Injects ``reasoning_content`` into assistant messages when present
        so that the reasoning chain can be replayed in multi-turn contexts.
        """
        return [self._serialize_message(msg) for msg in messages]

    def _serialize_message(self, message: ContextMessage) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": message.role,
            "content": message.content,
        }

        # Inject reasoning into assistant history
        if message.role == "assistant" and message.reasoning:
            payload[self.reasoning_key] = message.reasoning

        if message.role == "assistant" and message.metadata is not None:
            tool_calls_raw = message.metadata.get("tool_calls")
            if isinstance(tool_calls_raw, list):
                tool_calls = self._serialize_assistant_tool_calls(tool_calls_raw)
                if tool_calls:
                    payload["tool_calls"] = tool_calls

        if message.role == "tool" and message.metadata is not None:
            tool_call_id = message.metadata.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id:
                payload["tool_call_id"] = tool_call_id

        return payload

    # -- Parsing helpers --

    def _extract_first_choice(self, body: dict[str, object]) -> dict[str, object]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderBadResponseError("Provider response has no choices")

        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderBadResponseError("Provider response choice is invalid")
        return first

    def _parse_tool_calls(self, payload: object) -> list[ToolCall]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise ProviderBadResponseError("Invalid tool_calls payload from provider")

        calls: list[ToolCall] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ProviderBadResponseError("Tool call entry is not an object")

            call_id_raw = item.get("id")
            function_raw = item.get("function")
            if not isinstance(call_id_raw, str) or not isinstance(function_raw, dict):
                raise ProviderBadResponseError("Tool call entry missing id/function")

            name_raw = function_raw.get("name")
            arguments_raw = function_raw.get("arguments")
            if not isinstance(name_raw, str):
                raise ProviderBadResponseError("Tool call function name is invalid")

            parsed_arguments: dict[str, object] = {}
            if isinstance(arguments_raw, str) and arguments_raw.strip():
                try:
                    loaded = json.loads(arguments_raw)
                    if isinstance(loaded, dict):
                        parsed_arguments = loaded
                    else:
                        raise ProviderBadResponseError(
                            "Tool call arguments must decode to JSON object"
                        )
                except json.JSONDecodeError as exc:
                    raise ProviderBadResponseError(
                        "Tool call arguments are not valid JSON"
                    ) from exc

            calls.append(
                ToolCall(
                    call_id=call_id_raw,
                    name=name_raw,
                    arguments=parsed_arguments,
                )
            )
        return calls

    def _parse_usage(self, body: dict[str, object]) -> TokenUsage | None:
        """Extract token usage statistics from response body."""
        usage_raw = body.get("usage")
        if not isinstance(usage_raw, dict):
            return None

        input_tokens = usage_raw.get("prompt_tokens", 0)
        output_tokens = usage_raw.get("completion_tokens", 0)

        # Extract reasoning tokens from nested completion_tokens_details
        reasoning_tokens = 0
        details = usage_raw.get("completion_tokens_details")
        if isinstance(details, dict):
            rt = details.get("reasoning_tokens", 0)
            if isinstance(rt, int):
                reasoning_tokens = rt

        # DeepSeek cache tokens
        cached_tokens = 0
        prompt_details = usage_raw.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            ct = prompt_details.get("cached_tokens", 0)
            if isinstance(ct, int):
                cached_tokens = ct
        # DeepSeek alternative cache field
        cache_hit = usage_raw.get("prompt_cache_hit_tokens")
        if isinstance(cache_hit, int) and cache_hit > 0:
            cached_tokens = cache_hit

        return TokenUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    def _serialize_assistant_tool_calls(
        self,
        payload: list[object],
    ) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue

            call_id = item.get("id")
            name = item.get("name")
            arguments = item.get("arguments", {})

            if not isinstance(call_id, str) or not isinstance(name, str):
                continue
            if not isinstance(arguments, dict):
                continue

            serialized.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )

        return serialized
