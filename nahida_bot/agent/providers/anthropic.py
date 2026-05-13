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
import structlog

from nahida_bot.agent.context import ContextMessage, ContextPart
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

logger = structlog.get_logger(__name__)

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
    stream_responses: bool = False
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
                result.append(self._serialize_user_message(msg))

        return system_prompt, result

    def _serialize_user_message(self, msg: ContextMessage) -> dict[str, object]:
        if not msg.parts:
            return {"role": "user", "content": msg.content}

        blocks: list[dict[str, object]] = []
        for part in msg.parts:
            block = self._serialize_user_part(part)
            if block is not None:
                blocks.append(block)

        if not blocks:
            blocks.append({"type": "text", "text": msg.content})
        return {"role": "user", "content": blocks}

    def _serialize_user_part(self, part: ContextPart) -> dict[str, object] | None:
        if part.type in {"text", "image_description"}:
            if not part.text:
                return None
            return {"type": "text", "text": part.text}

        if part.type == "image_url":
            if not part.url:
                return None
            block: dict[str, object] = {
                "type": "image",
                "source": {
                    "type": "url",
                    "url": part.url,
                },
            }
            if part.cache_control:
                block["cache_control"] = {"type": part.cache_control}
            return block

        if part.type == "image_base64":
            if not part.data or not part.mime_type:
                return None
            block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": part.mime_type,
                    "data": part.data,
                },
            }
            if part.cache_control:
                block["cache_control"] = {"type": part.cache_control}
            return block

        return None

    def _serialize_assistant_message(self, msg: ContextMessage) -> dict[str, object]:
        """Serialize an assistant ContextMessage into Anthropic format.

        Injects ``thinking`` / ``redacted_thinking`` blocks when the message
        carries reasoning content.  For Claude, the ``signature`` field is
        required; for Anthropic-compatible backends (e.g. Minimax) that don't
        emit signatures, the thinking block is replayed without one.
        """
        blocks: list[dict[str, object]] = []

        # Inject thinking block when reasoning content exists.
        # Claude provides a signature — Minimax and other compatible backends
        # may not.  Both must replay thinking for correct multi-turn context.
        if msg.reasoning_signature:
            blocks.append(
                {
                    "type": "thinking",
                    "thinking": msg.reasoning or "",
                    "signature": msg.reasoning_signature,
                }
            )
            if msg.has_redacted_thinking:
                blocks.append(
                    {
                        "type": "redacted_thinking",
                        "signature": msg.reasoning_signature,
                    }
                )
        elif msg.reasoning:
            # Non-Claude backend (e.g. Minimax) — no signature available.
            blocks.append(
                {
                    "type": "thinking",
                    "thinking": msg.reasoning,
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
        model: str | None = None,
    ) -> ProviderResponse:
        """Call the Anthropic Messages API."""
        system_prompt, serialized_messages = self._serialize_messages_anthropic(
            messages
        )

        payload: dict[str, object] = {
            "model": model or self.model,
            "max_tokens": self.max_tokens,
            "messages": serialized_messages,
        }
        if system_prompt is not None:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = self.format_tools(tools)
        if self.stream_responses:
            payload["stream"] = True

        timeout = timeout_seconds or 60
        endpoint = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if self.stream_responses:
            headers["Accept"] = "text/event-stream"

        try:
            logger.debug(
                "provider.anthropic.request",
                provider_name=self.name,
                base_url=self.base_url,
                endpoint="/v1/messages",
                model=payload["model"],
                message_count=len(messages),
                serialized_message_count=len(serialized_messages),
                has_system=system_prompt is not None,
                tool_count=len(tools or []),
                stream=self.stream_responses,
                timeout_seconds=timeout,
            )
            client = self._ensure_client()
            if self.stream_responses:
                body = await self._stream_messages(
                    client=client,
                    endpoint=endpoint,
                    payload=payload,
                    headers=headers,
                    timeout=timeout,
                )
                status_code = 200
            else:
                response = await client.post(
                    endpoint, json=payload, headers=headers, timeout=timeout
                )
                self._raise_for_status(response)
                status_code = response.status_code
                try:
                    body = response.json()
                except ValueError as exc:
                    raise ProviderBadResponseError(
                        "Provider returned non-JSON body"
                    ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise ProviderTransportError(
                f"HTTP transport error communicating with {self.name}"
            ) from exc

        logger.debug(
            "provider.anthropic.response",
            provider_name=self.name,
            model=payload["model"],
            status_code=status_code,
            stream=self.stream_responses,
        )

        return self._parse_response(body)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"Provider auth rejected request with status {response.status_code}"
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

    async def _raise_for_stream_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        raw = await response.aread()
        body_hint = raw.decode("utf-8", errors="replace")
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"Provider auth rejected request with status {response.status_code} — {body_hint[:200]}"
            )
        if response.status_code == 429:
            raise ProviderRateLimitError()
        if response.status_code >= 500:
            raise ProviderTransportError(
                f"Provider server error: status {response.status_code} — {body_hint[:200]}"
            )
        raise ProviderBadResponseError(
            f"Provider rejected request: status {response.status_code} — {body_hint[:300]}"
        )

    async def _stream_messages(
        self,
        *,
        client: httpx.AsyncClient,
        endpoint: str,
        payload: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, object]:
        async with client.stream(
            "POST", endpoint, json=payload, headers=headers, timeout=timeout
        ) as response:
            await self._raise_for_stream_status(response)
            return await self._parse_stream_response(response)

    async def _parse_stream_response(
        self, response: httpx.Response
    ) -> dict[str, object]:
        message: dict[str, object] = {
            "type": "message",
            "role": "assistant",
            "content": [],
        }
        content_by_index: dict[int, dict[str, object]] = {}
        tool_input_json: dict[int, str] = {}
        usage: dict[str, object] = {}
        stop_reason: str | None = None

        async for raw_line in response.aiter_lines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data:
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            event_type = event.get("type")
            if event_type == "message_start":
                raw_message = event.get("message")
                if isinstance(raw_message, dict):
                    message.update(
                        {
                            key: value
                            for key, value in raw_message.items()
                            if key != "content"
                        }
                    )
                    raw_usage = raw_message.get("usage")
                    if isinstance(raw_usage, dict):
                        usage.update(raw_usage)
            elif event_type == "content_block_start":
                index = self._stream_event_index(event)
                block = event.get("content_block")
                if index is not None and isinstance(block, dict):
                    content_by_index[index] = dict(block)
            elif event_type == "content_block_delta":
                index = self._stream_event_index(event)
                delta = event.get("delta")
                if index is not None and isinstance(delta, dict):
                    self._apply_content_block_delta(
                        index=index,
                        block=content_by_index.setdefault(index, {}),
                        delta=delta,
                        tool_input_json=tool_input_json,
                    )
            elif event_type == "message_delta":
                delta = event.get("delta")
                if isinstance(delta, dict):
                    raw_stop = delta.get("stop_reason")
                    if isinstance(raw_stop, str):
                        stop_reason = raw_stop
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    usage.update(raw_usage)

        for index, raw_json in tool_input_json.items():
            block = content_by_index.setdefault(index, {"type": "tool_use"})
            try:
                parsed = json.loads(raw_json) if raw_json.strip() else {}
            except json.JSONDecodeError:
                parsed = {}
            block["input"] = parsed if isinstance(parsed, dict) else {}

        content = [content_by_index[i] for i in sorted(content_by_index)]
        if not content:
            raise ProviderBadResponseError("Streaming response missing content blocks")

        message["content"] = content
        if stop_reason is not None:
            message["stop_reason"] = stop_reason
        elif isinstance(message.get("stop_reason"), str):
            pass
        else:
            message["stop_reason"] = "end_turn"
        if usage:
            message["usage"] = usage
        return message

    def _stream_event_index(self, event: dict[str, object]) -> int | None:
        index = event.get("index")
        return index if isinstance(index, int) else None

    def _apply_content_block_delta(
        self,
        *,
        index: int,
        block: dict[str, object],
        delta: dict[str, object],
        tool_input_json: dict[int, str],
    ) -> None:
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text")
            if isinstance(text, str):
                block["type"] = block.get("type") or "text"
                block["text"] = str(block.get("text", "")) + text
        elif delta_type == "thinking_delta":
            thinking = delta.get("thinking")
            if isinstance(thinking, str):
                block["type"] = block.get("type") or "thinking"
                block["thinking"] = str(block.get("thinking", "")) + thinking
        elif delta_type == "signature_delta":
            signature = delta.get("signature")
            if isinstance(signature, str):
                block["signature"] = signature
        elif delta_type == "input_json_delta":
            partial = delta.get("partial_json")
            if isinstance(partial, str):
                block["type"] = block.get("type") or "tool_use"
                tool_input_json[index] = tool_input_json.get(index, "") + partial

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

        block_types = [
            block.get("type") if isinstance(block, dict) else type(block).__name__
            for block in content_blocks
        ]
        if finish_reason == "tool_calls" and not tool_calls:
            logger.warning(
                "provider.anthropic.tool_finish_without_parsed_calls",
                provider_name=self.name,
                model=self.model,
                stop_reason=stop_reason_raw if isinstance(stop_reason_raw, str) else "",
                block_types=block_types[:20],
                content_preview=(content or "")[:200],
            )
        else:
            logger.debug(
                "provider.anthropic.parsed_response",
                provider_name=self.name,
                model=self.model,
                stop_reason=stop_reason_raw if isinstance(stop_reason_raw, str) else "",
                finish_reason=finish_reason or "",
                block_types=block_types[:20],
                content_chars=len(content or ""),
                reasoning_chars=len(reasoning_content or ""),
                parsed_tool_call_count=len(tool_calls),
                has_redacted_thinking=has_redacted_thinking,
            )

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
        cache_creation = usage_raw.get("cache_creation_input_tokens", 0)

        return TokenUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
            cached_tokens=cache_read if isinstance(cache_read, int) else 0,
            cache_creation_tokens=(
                cache_creation if isinstance(cache_creation, int) else 0
            ),
        )
