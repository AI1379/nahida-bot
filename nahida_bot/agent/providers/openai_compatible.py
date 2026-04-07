"""OpenAI-compatible provider implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from nahida_bot.agent.context import ContextMessage
from nahida_bot.agent.providers.base import (
    ChatProvider,
    ProviderResponse,
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
from nahida_bot.agent.tokenization import Tokenizer


@dataclass(slots=True)
class OpenAICompatibleProvider(ChatProvider):
    """Provider for OpenAI-compatible `/chat/completions` endpoints."""

    base_url: str
    api_key: str
    model: str
    name: str = "openai-compatible"
    tokenizer_impl: Tokenizer | None = None

    @property
    def tokenizer(self) -> Tokenizer | None:
        """Expose provider tokenizer to context budgeting."""
        return self.tokenizer_impl

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
    ) -> ProviderResponse:
        """Call OpenAI-compatible chat completion API."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
        }
        if tools:
            payload["tools"] = [
                {
                    "type": tool.type,
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]

        timeout = timeout_seconds or 30
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(str(exc)) from exc

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

        content = message.get("content")
        normalized_content = content if isinstance(content, str) else None
        finish_reason_raw = choice.get("finish_reason")
        finish_reason = (
            finish_reason_raw if isinstance(finish_reason_raw, str) else None
        )

        tool_calls_payload = message.get("tool_calls")
        tool_calls = self._parse_tool_calls(tool_calls_payload)

        return ProviderResponse(
            content=normalized_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_response=body,
        )

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
