"""OpenAI-compatible provider implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import structlog

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

logger = structlog.get_logger(__name__)


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
    merge_system_messages: bool = False
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @property
    def tokenizer(self) -> Tokenizer | None:
        """Expose provider tokenizer to context budgeting."""
        return self.tokenizer_impl

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it if needed.

        TODO: The httpx.AsyncClient is never closed automatically — ``close()``
        must be called manually. Tie provider lifecycle to Application shutdown
        or implement ``__aenter__``/``__aexit__`` on ChatProvider so the AgentLoop
        can guarantee cleanup.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _extra_payload(self) -> dict[str, object]:
        """Hook for subclasses to inject provider-specific parameters."""
        return {}

    async def chat(
        self,
        *,
        messages: list[ContextMessage],
        tools: list[ToolDefinition] | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
    ) -> ProviderResponse:
        """Call OpenAI-compatible chat completion API."""
        prepared = (
            ChatProvider._coalesce_system_messages(messages)
            if self.merge_system_messages
            else messages
        )
        serialized_messages = self.serialize_messages(prepared)
        protocol_issues = self._serialized_protocol_issues(serialized_messages)
        protocol_log = logger.warning if protocol_issues else logger.debug
        protocol_log(
            "provider.openai_compatible.serialized_protocol",
            provider_name=self.name,
            model=model or self.model,
            issue_count=len(protocol_issues),
            issues=protocol_issues,
            summary=self._serialized_protocol_summary(serialized_messages),
        )

        payload: dict[str, object] = {
            "model": model or self.model,
            "messages": serialized_messages,
            **self._extra_payload(),
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
            logger.debug(
                "provider.openai_compatible.request",
                provider_name=self.name,
                base_url=self.base_url,
                endpoint="/chat/completions",
                model=payload["model"],
                message_count=len(messages),
                serialized_message_count=len(payload["messages"]),  # type: ignore[arg-type]
                tool_count=len(tools or []),
                timeout_seconds=timeout,
            )
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
            body_hint = response.text[:200] if response.text else ""
            raise ProviderAuthError(
                f"Provider auth rejected request with status {response.status_code} — {body_hint}"
            )
        if response.status_code == 429:
            raise ProviderRateLimitError()
        if response.status_code >= 500:
            body_hint = response.text[:200] if response.text else ""
            raise ProviderTransportError(
                f"Provider server error: status {response.status_code} — {body_hint}"
            )
        if response.status_code >= 400:
            body_hint = response.text[:300] if response.text else ""
            raise ProviderBadResponseError(
                f"Provider rejected request: status {response.status_code} — {body_hint}"
            )
        logger.debug(
            "provider.openai_compatible.response",
            provider_name=self.name,
            model=payload["model"],
            status_code=response.status_code,
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
        raw_tool_call_count = (
            len(tool_calls_payload) if isinstance(tool_calls_payload, list) else 0
        )

        # --- Extract usage statistics ---
        usage = self._parse_usage(body)

        if finish_reason == "tool_calls" and not tool_calls:
            logger.warning(
                "provider.openai_compatible.tool_finish_without_parsed_calls",
                provider_name=self.name,
                model=model or self.model,
                message_keys=sorted(message.keys()),
                has_tool_calls_payload=isinstance(tool_calls_payload, list),
                raw_tool_call_count=raw_tool_call_count,
                content_preview=(normalized_content or "")[:200],
            )
        else:
            logger.debug(
                "provider.openai_compatible.parsed_response",
                provider_name=self.name,
                model=model or self.model,
                finish_reason=finish_reason or "",
                content_chars=len(normalized_content or ""),
                reasoning_chars=len(reasoning_content or ""),
                has_tool_calls_payload=isinstance(tool_calls_payload, list),
                raw_tool_call_count=raw_tool_call_count,
                parsed_tool_call_count=len(tool_calls),
                message_keys=sorted(message.keys()),
            )

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
        return [
            self._serialize_message(msg)
            for msg in self._sanitize_tool_transcript(messages)
        ]

    def _sanitize_tool_transcript(
        self,
        messages: list[ContextMessage],
    ) -> list[ContextMessage]:
        """Drop broken tool-call fragments before sending chat-completion history.

        OpenAI-compatible APIs require an assistant message with ``tool_calls`` to
        be followed immediately by tool messages for every emitted
        ``tool_call_id``. Context budgeting can otherwise leave only one side of
        the pair in the prompt, which causes a 400 before the model can recover.
        """
        sanitized: list[ContextMessage] = []
        dropped_orphan_tools = 0
        dropped_incomplete_groups = 0
        index = 0

        while index < len(messages):
            message = messages[index]
            required_tool_call_ids = self._assistant_tool_call_ids(message)
            if not required_tool_call_ids:
                if message.role == "tool":
                    dropped_orphan_tools += 1
                else:
                    sanitized.append(message)
                index += 1
                continue

            tool_group: list[ContextMessage] = []
            seen_ids: set[str] = set()
            next_index = index + 1
            while next_index < len(messages) and messages[next_index].role == "tool":
                tool_message = messages[next_index]
                tool_call_id = self._tool_message_call_id(tool_message)
                if tool_call_id in required_tool_call_ids:
                    tool_group.append(tool_message)
                    seen_ids.add(tool_call_id)
                else:
                    dropped_orphan_tools += 1
                next_index += 1

            if required_tool_call_ids.issubset(seen_ids):
                sanitized.append(message)
                sanitized.extend(tool_group)
            else:
                dropped_incomplete_groups += 1
                dropped_orphan_tools += len(tool_group)
                logger.warning(
                    "provider.openai_compatible.dropped_incomplete_tool_transcript",
                    provider_name=self.name,
                    assistant_source=message.source,
                    required_tool_call_ids=sorted(required_tool_call_ids),
                    seen_tool_call_ids=sorted(seen_ids),
                    missing_tool_call_ids=sorted(required_tool_call_ids - seen_ids),
                )

            index = next_index

        if dropped_orphan_tools:
            logger.warning(
                "provider.openai_compatible.dropped_orphan_tool_messages",
                provider_name=self.name,
                dropped_tool_message_count=dropped_orphan_tools,
            )

        if dropped_orphan_tools or dropped_incomplete_groups:
            logger.debug(
                "provider.openai_compatible.sanitized_tool_transcript",
                provider_name=self.name,
                original_message_count=len(messages),
                sanitized_message_count=len(sanitized),
                dropped_orphan_tool_count=dropped_orphan_tools,
                dropped_incomplete_group_count=dropped_incomplete_groups,
                original_roles=[message.role for message in messages],
                sanitized_roles=[message.role for message in sanitized],
            )

        return sanitized

    def _assistant_tool_call_ids(self, message: ContextMessage) -> set[str]:
        if message.role != "assistant" or message.metadata is None:
            return set()
        raw_tool_calls = message.metadata.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return set()

        ids: set[str] = set()
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                ids.add(call_id)
        return ids

    def _tool_message_call_id(self, message: ContextMessage) -> str:
        if message.role != "tool" or message.metadata is None:
            return ""
        call_id = message.metadata.get("tool_call_id")
        return call_id if isinstance(call_id, str) else ""

    def _serialized_protocol_summary(
        self,
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        assistant_tool_call_ids: list[list[str]] = []
        tool_call_ids: list[str] = []
        tool_messages_missing_ids = 0

        for message in messages:
            role = message.get("role")
            if role == "assistant":
                raw_tool_calls = message.get("tool_calls")
                if isinstance(raw_tool_calls, list):
                    ids: list[str] = []
                    for item in raw_tool_calls:
                        if not isinstance(item, dict):
                            continue
                        call_id = item.get("id")
                        if isinstance(call_id, str):
                            ids.append(call_id)
                    if ids:
                        assistant_tool_call_ids.append(ids)
            elif role == "tool":
                call_id = message.get("tool_call_id")
                if isinstance(call_id, str) and call_id:
                    tool_call_ids.append(call_id)
                else:
                    tool_messages_missing_ids += 1

        return {
            "roles": [message.get("role", "") for message in messages],
            "assistant_tool_call_ids": assistant_tool_call_ids,
            "tool_call_ids": tool_call_ids,
            "tool_messages_missing_ids": tool_messages_missing_ids,
        }

    def _serialized_protocol_issues(
        self,
        messages: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        issues: list[dict[str, object]] = []
        pending_ids: set[str] = set()
        pending_index: int | None = None

        for index, message in enumerate(messages):
            role = message.get("role")

            if pending_ids and role != "tool":
                issues.append(
                    {
                        "type": "assistant_tool_calls_missing_tool_messages",
                        "assistant_index": pending_index,
                        "before_index": index,
                        "missing_tool_call_ids": sorted(pending_ids),
                    }
                )
                pending_ids = set()
                pending_index = None

            if role == "assistant":
                tool_call_ids = self._serialized_assistant_tool_call_ids(message)
                if tool_call_ids:
                    pending_ids = set(tool_call_ids)
                    pending_index = index
            elif role == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id:
                    issues.append(
                        {
                            "type": "tool_message_missing_tool_call_id",
                            "index": index,
                        }
                    )
                elif not pending_ids:
                    issues.append(
                        {
                            "type": "orphan_tool_message",
                            "index": index,
                            "tool_call_id": call_id,
                        }
                    )
                elif call_id in pending_ids:
                    pending_ids.remove(call_id)
                else:
                    issues.append(
                        {
                            "type": "unexpected_tool_call_id",
                            "index": index,
                            "tool_call_id": call_id,
                            "pending_tool_call_ids": sorted(pending_ids),
                        }
                    )

        if pending_ids:
            issues.append(
                {
                    "type": "assistant_tool_calls_missing_tool_messages",
                    "assistant_index": pending_index,
                    "before_index": None,
                    "missing_tool_call_ids": sorted(pending_ids),
                }
            )

        return issues

    def _serialized_assistant_tool_call_ids(
        self,
        message: dict[str, object],
    ) -> list[str]:
        raw_tool_calls = message.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return []
        ids: list[str] = []
        for item in raw_tool_calls:
            if not isinstance(item, dict):
                continue
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                ids.append(call_id)
        return ids

    def _serialize_message(self, message: ContextMessage) -> dict[str, object]:
        payload: dict[str, object] = {
            "role": message.role,
            "content": self._serialize_openai_content(message),
        }

        # Inject reasoning into assistant history
        if message.role == "assistant" and message.reasoning:
            payload[self.reasoning_key] = message.reasoning

        if message.role == "assistant" and message.metadata is not None:
            tool_calls_raw = message.metadata.get("tool_calls")
            if isinstance(tool_calls_raw, list):
                tool_calls = self._serialize_assistant_tool_calls(tool_calls_raw)
                if tool_calls:
                    if not message.content and not message.parts:
                        payload["content"] = None
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
