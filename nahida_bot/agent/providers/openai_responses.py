"""OpenAI Responses API provider — independent implementation.

The Responses API (``POST /responses``) uses a fundamentally different
request/response format from ``/chat/completions``:

- ``input`` array instead of ``messages`` (with ``input_text``/``output_text`` types)
- Flat ``output`` array instead of ``choices[].message``
- Built-in tools: web_search, file_search, code_interpreter, image_generation
- Stateful chaining via ``previous_response_id``
- ``reasoning`` parameter instead of ``reasoning_content`` field
- ``developer`` role instead of ``system``

This provider is therefore implemented independently and does **not** inherit
from ``OpenAICompatibleProvider``.
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

# Responses API status → normalised finish_reason mapping
_STATUS_MAP: dict[str, str] = {
    "completed": "stop",
    "incomplete": "length",
    "failed": "error",
}


@register_provider("openai-responses", "OpenAI Responses API Provider")
@dataclass(slots=True)
class OpenAIResponsesProvider(ChatProvider):
    """Provider for the OpenAI Responses API (``POST /responses``).

    Does **not** inherit ``OpenAICompatibleProvider`` because the Responses API
    uses a completely different request/response structure (input array, output
    array, built-in tools, stateful chaining).
    """

    base_url: str
    api_key: str
    model: str
    name: str = "openai-responses"
    api_family: str = "openai-responses"
    max_output_tokens: int | None = None
    store_responses: bool = False
    use_previous_response_id: bool = False
    stream_responses: bool = False
    reasoning_effort: str | None = None
    built_in_tools: list[object] | None = None
    tokenizer_impl: Tokenizer | None = None
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    @property
    def tokenizer(self) -> Tokenizer | None:
        return self.tokenizer_impl

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # serialize_messages: ContextMessage[] → Responses API input array
    # ------------------------------------------------------------------

    def serialize_messages(
        self, messages: list[ContextMessage]
    ) -> list[dict[str, object]]:
        """Convert ContextMessage list to Responses API input format.

        The Responses API uses an ``input`` array with content type names
        that differ from Chat Completions (``input_text``/``output_text`` vs
        ``text``, ``input_image`` vs ``image_url``).
        """
        result: list[dict[str, object]] = []
        for msg in messages:
            if msg.role == "system":
                continue
            items = self._serialize_input_item(msg)
            result.extend(items)
        return result

    def _serialize_input_item(self, msg: ContextMessage) -> list[dict[str, object]]:
        if msg.role == "user":
            return [self._serialize_user_message(msg)]
        if msg.role == "assistant":
            return self._serialize_assistant_message(msg)
        if msg.role == "tool":
            return [self._serialize_tool_result_message(msg)]
        return []

    def _serialize_user_message(self, msg: ContextMessage) -> dict[str, object]:
        if not msg.parts:
            return {
                "role": "user",
                "content": [{"type": "input_text", "text": msg.content}],
            }

        blocks: list[dict[str, object]] = []
        for part in msg.parts:
            block = self._serialize_user_part(part)
            if block is not None:
                blocks.append(block)

        if not blocks:
            return {
                "role": "user",
                "content": [{"type": "input_text", "text": msg.content}],
            }
        return {"role": "user", "content": blocks}

    def _serialize_user_part(self, part: ContextPart) -> dict[str, object] | None:
        if part.type in {"text", "image_description"}:
            if not part.text:
                return None
            return {"type": "input_text", "text": part.text}

        if part.type == "image_url":
            if not part.url:
                return None
            return {"type": "input_image", "image_url": part.url}

        if part.type == "image_base64":
            if not part.data or not part.mime_type:
                return None
            return {
                "type": "input_image",
                "image_url": f"data:{part.mime_type};base64,{part.data}",
            }

        return None

    def _serialize_assistant_message(
        self, msg: ContextMessage
    ) -> list[dict[str, object]]:
        if msg.metadata is not None:
            raw_output = msg.metadata.get("response_output")
            if isinstance(raw_output, list):
                return self._sanitize_replay_output(raw_output)

        items: list[dict[str, object]] = []
        if msg.content:
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": msg.content}],
                }
            )

        if msg.reasoning:
            items.append(
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": msg.reasoning}],
                }
            )

        if msg.metadata is not None:
            tool_calls_raw = msg.metadata.get("tool_calls")
            if isinstance(tool_calls_raw, list):
                for tc in tool_calls_raw:
                    if not isinstance(tc, dict):
                        continue
                    call_id = tc.get("id")
                    name = tc.get("name")
                    arguments = tc.get("arguments", {})
                    if isinstance(call_id, str) and isinstance(name, str):
                        args_str = (
                            json.dumps(arguments, ensure_ascii=False)
                            if isinstance(arguments, dict)
                            else str(arguments)
                        )
                        items.append(
                            {
                                "type": "function_call",
                                "id": call_id,
                                "call_id": call_id,
                                "name": name,
                                "arguments": args_str,
                            }
                        )

        if not items:
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": ""}],
                }
            )

        return items

    def _serialize_tool_result_message(self, msg: ContextMessage) -> dict[str, object]:
        tool_call_id = ""
        if msg.metadata is not None:
            raw_id = msg.metadata.get("tool_call_id")
            if isinstance(raw_id, str):
                tool_call_id = raw_id

        return {
            "type": "function_call_output",
            "call_id": tool_call_id,
            "output": msg.content,
        }

    def _sanitize_replay_output(
        self, raw_output: list[object]
    ) -> list[dict[str, object]]:
        replay: list[dict[str, object]] = []
        for item in raw_output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type not in {"message", "function_call", "reasoning"}:
                continue
            replay.append(dict(item))
        return replay

    def _instructions_from_messages(self, messages: list[ContextMessage]) -> str:
        parts: list[str] = []
        for msg in messages:
            if msg.role == "system" and msg.content:
                if msg.source == "combined_system":
                    parts.append(msg.content)
                else:
                    parts.append(
                        f"**{msg.source}**\n\n{msg.content}"
                        if msg.source
                        else msg.content
                    )
        return "\n\n".join(parts)

    def _input_messages_for_request(
        self, messages: list[ContextMessage]
    ) -> tuple[str | None, list[ContextMessage]]:
        non_system = [msg for msg in messages if msg.role != "system"]
        if not self.store_responses or not self.use_previous_response_id:
            return None, non_system

        for index in range(len(non_system) - 1, -1, -1):
            msg = non_system[index]
            if msg.role != "assistant" or msg.metadata is None:
                continue
            response_id = msg.metadata.get("response_id")
            if isinstance(response_id, str) and response_id:
                return response_id, non_system[index + 1 :]
        return None, non_system

    # ------------------------------------------------------------------
    # format_tools: ToolDefinition[] + built-in tools → Responses API format
    # ------------------------------------------------------------------

    def format_tools(self, tools: list[ToolDefinition]) -> list[object]:
        result: list[object] = []

        # User-defined function tools
        for tool in tools:
            result.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            )

        # Built-in tools from config
        for builtin in self.built_in_tools or []:
            formatted = self._format_builtin_tool(builtin)
            if formatted is not None:
                result.append(formatted)

        return result

    def _format_builtin_tool(self, builtin: object) -> dict[str, object] | None:
        if isinstance(builtin, dict):
            tool_type = builtin.get("type")
            if isinstance(tool_type, str) and tool_type:
                return dict(builtin)
            return None

        if not isinstance(builtin, str):
            return None

        tool_type = builtin.strip()
        if not tool_type:
            return None

        # OpenAI Responses API uses web_search in the current docs.
        # Keep web_search_preview as a compatibility alias for older setups.
        if tool_type in {"web_search", "web_search_preview"}:
            return {"type": "web_search"}
        if tool_type in {
            "file_search",
            "code_interpreter",
            "image_generation",
        }:
            return {"type": tool_type}
        return None

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
        prepared = ChatProvider._coalesce_system_messages(messages)
        previous_response_id, input_messages = self._input_messages_for_request(
            prepared
        )
        input_items = self.serialize_messages(input_messages)

        payload: dict[str, object] = {
            "model": model or self.model,
            "input": input_items,
        }
        instructions = self._instructions_from_messages(prepared)
        if instructions:
            payload["instructions"] = instructions

        if self.store_responses:
            payload["store"] = True
        if previous_response_id is not None:
            payload["previous_response_id"] = previous_response_id

        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}

        if self.max_output_tokens is not None:
            payload["max_output_tokens"] = self.max_output_tokens

        if tools:
            payload["tools"] = self.format_tools(tools)
        elif self.built_in_tools:
            payload["tools"] = self.format_tools([])

        if self.stream_responses:
            payload["stream"] = True

        timeout = timeout_seconds or 60
        endpoint = f"{self.base_url.rstrip('/')}/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.stream_responses:
            headers["Accept"] = "text/event-stream"

        try:
            logger.debug(
                "provider.openai_responses.request",
                provider_name=self.name,
                base_url=self.base_url,
                endpoint="/responses",
                model=payload["model"],
                message_count=len(messages),
                input_item_count=len(input_items),
                tool_count=len(tools or []),
                store=self.store_responses,
                stream=self.stream_responses,
                has_previous_response_id=previous_response_id is not None,
                reasoning_effort=self.reasoning_effort,
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
            "provider.openai_responses.response",
            provider_name=self.name,
            model=payload["model"],
            status_code=response.status_code,
        )

        if self.stream_responses:
            return self._parse_stream_response(response.text)

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderBadResponseError("Provider returned non-JSON body") from exc
        return self._parse_response(body)

    def _parse_stream_response(self, text: str) -> ProviderResponse:
        text_parts: list[str] = []
        done_text: str | None = None
        final_body: dict[str, object] | None = None
        output_items: list[object] = []

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            event_type = event.get("type")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif event_type == "response.output_text.done":
                text_done = event.get("text")
                if isinstance(text_done, str):
                    done_text = text_done
            elif event_type == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict):
                    output_items.append(item)
            elif event_type == "response.completed":
                response = event.get("response")
                if isinstance(response, dict):
                    final_body = response

        if final_body is None:
            raise ProviderBadResponseError(
                "Streaming response missing completion event"
            )

        content = done_text if done_text is not None else "".join(text_parts)
        if content:
            final_body = dict(final_body)
            final_body["output_text"] = content
        if output_items:
            final_body = dict(final_body)
            final_body["output"] = output_items

        return self._parse_response(final_body)

    # ------------------------------------------------------------------
    # Response parsing — flat output array
    # ------------------------------------------------------------------

    def _parse_response(self, body: dict[str, object]) -> ProviderResponse:
        output_items = body.get("output")
        if not isinstance(output_items, list):
            raise ProviderBadResponseError(
                "Responses API response missing output array"
            )

        text_parts: list[str] = []
        refusal_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        generated_images: list[dict[str, object]] = []
        web_search_calls: list[dict[str, object]] = []
        reasoning_parts: list[str] = []

        for item in output_items:
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")

            if item_type == "message":
                self._extract_message_content(item, text_parts, refusal_parts)
            elif item_type == "output_text":
                self._extract_output_text_item(item, text_parts)
            elif item_type == "function_call":
                tc = self._parse_function_call(item)
                if tc is not None:
                    tool_calls.append(tc)
            elif item_type == "reasoning":
                self._extract_reasoning(item, reasoning_parts)
            elif item_type == "image_generation_call":
                generated_images.append(self._parse_image_generation(item))

            # Track web search calls for pass-through
            if item_type in ("web_search_call", "file_search_call"):
                web_search_calls.append(item)

        # Normalise finish reason from top-level status
        status_raw = body.get("status")
        finish_reason: str | None = None
        if isinstance(status_raw, str):
            finish_reason = _STATUS_MAP.get(status_raw, status_raw)

        output_text = body.get("output_text")
        if not text_parts and isinstance(output_text, str) and output_text:
            text_parts.insert(0, output_text)

        content = "\n".join(text_parts) if text_parts else None
        refusal = "\n".join(refusal_parts) if refusal_parts else None
        reasoning_content = "\n".join(reasoning_parts) if reasoning_parts else None

        usage = self._parse_usage(body)

        extra: dict[str, object] = {}
        response_id = body.get("id")
        if isinstance(response_id, str):
            extra["response_id"] = response_id
        response_output = self._extract_replay_output(output_items)
        if response_output:
            extra["response_output"] = response_output
        if generated_images:
            extra["generated_images"] = generated_images
        if web_search_calls:
            extra["builtin_tool_calls"] = web_search_calls
        if content is None and refusal is None:
            extra["response_shape"] = self._response_shape(body, output_items)
            logger.warning(
                "provider.openai_responses.empty_content",
                provider_name=self.name,
                status=finish_reason or "",
                output_types=extra["response_shape"].get("output_types", []),
                content_types=extra["response_shape"].get("content_types", []),
                has_output_text=extra["response_shape"].get("has_output_text", False),
                usage=body.get("usage"),
            )

        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw_response=body,
            reasoning_content=reasoning_content,
            refusal=refusal,
            usage=usage,
            extra=extra,
        )

    def _extract_replay_output(
        self, output_items: list[object]
    ) -> list[dict[str, object]]:
        replay: list[dict[str, object]] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type not in {"message", "function_call", "reasoning"}:
                continue
            replay.append(dict(item))
        return replay

    def _extract_message_content(
        self,
        item: dict[str, object],
        text_parts: list[str],
        refusal_parts: list[str],
    ) -> None:
        content = item.get("content")
        if isinstance(content, str):
            text_parts.append(content)
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"output_text", "text"}:
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif block_type == "refusal":
                refusal = block.get("refusal")
                if isinstance(refusal, str):
                    refusal_parts.append(refusal)

    def _extract_output_text_item(
        self, item: dict[str, object], text_parts: list[str]
    ) -> None:
        text = item.get("text")
        if isinstance(text, str):
            text_parts.append(text)

    def _parse_function_call(self, item: dict[str, object]) -> ToolCall | None:
        call_id = item.get("call_id") or item.get("id")
        name = item.get("name")
        arguments_raw = item.get("arguments")

        if not isinstance(call_id, str) or not isinstance(name, str):
            return None

        arguments: dict[str, object] = {}
        if isinstance(arguments_raw, str) and arguments_raw.strip():
            try:
                loaded = json.loads(arguments_raw)
                if isinstance(loaded, dict):
                    arguments = loaded
                else:
                    raise ProviderBadResponseError(
                        "Tool call arguments must decode to JSON object"
                    )
            except json.JSONDecodeError as exc:
                raise ProviderBadResponseError(
                    "Tool call arguments are not valid JSON"
                ) from exc
        elif isinstance(arguments_raw, dict):
            arguments = arguments_raw

        return ToolCall(call_id=call_id, name=name, arguments=arguments)

    def _extract_reasoning(self, item: dict[str, object], parts: list[str]) -> None:
        summary = item.get("summary")
        if not isinstance(summary, list):
            return
        for entry in summary:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "summary_text":
                text = entry.get("text")
                if isinstance(text, str):
                    parts.append(text)

    def _parse_image_generation(self, item: dict[str, object]) -> dict[str, object]:
        result: dict[str, object] = {"type": "image_generation_call"}
        # The API may return a URL or base64 data
        if "result" in item:
            result["data"] = item["result"]
        if "revised_prompt" in item:
            result["revised_prompt"] = item["revised_prompt"]
        return result

    def _parse_usage(self, body: dict[str, object]) -> TokenUsage | None:
        usage_raw = body.get("usage")
        if not isinstance(usage_raw, dict):
            return None

        input_tokens = usage_raw.get("input_tokens", 0)
        output_tokens = usage_raw.get("output_tokens", 0)

        # Reasoning tokens from nested details
        reasoning_tokens = 0
        output_details = usage_raw.get("output_tokens_details")
        if isinstance(output_details, dict):
            rt = output_details.get("reasoning_tokens", 0)
            if isinstance(rt, int):
                reasoning_tokens = rt

        return TokenUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
            reasoning_tokens=reasoning_tokens,
        )

    def _response_shape(
        self, body: dict[str, object], output_items: list[object]
    ) -> dict[str, object]:
        output_types: list[str] = []
        content_types: list[str] = []
        for item in output_items:
            if not isinstance(item, dict):
                output_types.append(type(item).__name__)
                continue
            item_type = item.get("type")
            output_types.append(
                item_type if isinstance(item_type, str) else "<missing>"
            )
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block_type = block.get("type")
                        content_types.append(
                            block_type if isinstance(block_type, str) else "<missing>"
                        )
                    else:
                        content_types.append(type(block).__name__)
            elif content is not None:
                content_types.append(type(content).__name__)

        return {
            "output_types": output_types,
            "content_types": content_types,
            "has_output_text": isinstance(body.get("output_text"), str),
        }
