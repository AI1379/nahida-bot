"""Shared tool-transcript sanitization used by all chat providers.

Providers (OpenAI-compatible AND Anthropic-compatible, the latter being what
Minimax inherits) require an assistant message that emits ``tool_calls`` to be
followed by a tool message for *every* ``tool_call_id`` it declared. If history
truncation or a malformed replay leaves only one side of the pair in the prompt,
the API rejects the whole request with an HTTP 400 (e.g. Minimax error 2013:
``tool result's tool id(...) not found``) before the model can recover.

This module is the provider-side defense in depth: it runs just before
serialization and **drops** any protocol-invalid fragment — orphan tool results
(without a preceding assistant call) and incomplete assistant groups (whose
declared tool_call_ids are not all present). It only ever drops; it never
synthesizes. Pair preservation across truncation is the job of
``nahida_bot.agent.context.truncate_messages_to_window``; this is the last-chance
wire-format safety net so a regression elsewhere cannot produce a 400.

Import policy: this module imports only from ``nahida_bot.agent.context`` to
avoid any import cycle through ``providers.__init__``.
"""

from __future__ import annotations

import structlog

from nahida_bot.agent.context import (
    ContextMessage,
    assistant_tool_call_ids,
    tool_message_call_id,
)

logger = structlog.get_logger(__name__)


def sanitize_tool_transcript(
    messages: list[ContextMessage],
    *,
    provider_name: str,
) -> list[ContextMessage]:
    """Drop broken tool-call fragments before serializing chat history.

    Walks ``messages`` once. For each assistant turn that declared tool calls,
    collects the contiguous run of ``tool`` messages that follows, keeping only
    those whose ``tool_call_id`` was declared. If every declared id is accounted
    for, the assistant + its tool group survive; otherwise the whole group is
    dropped (better to omit the turn than to send an unanswerable call). Orphan
    tool messages (no preceding assistant call) are always dropped.
    """
    sanitized: list[ContextMessage] = []
    dropped_orphan_tools = 0
    dropped_incomplete_groups = 0
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        required_tool_call_ids = assistant_tool_call_ids(message)
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
        while next_index < total and messages[next_index].role == "tool":
            tool_message = messages[next_index]
            tool_call_id = tool_message_call_id(tool_message)
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
                "provider.tool_transcript.dropped_incomplete_group",
                provider_name=provider_name,
                assistant_source=message.source,
                required_tool_call_ids=sorted(required_tool_call_ids),
                seen_tool_call_ids=sorted(seen_ids),
                missing_tool_call_ids=sorted(required_tool_call_ids - seen_ids),
            )

        index = next_index

    if dropped_orphan_tools:
        logger.warning(
            "provider.tool_transcript.dropped_orphan_tool_messages",
            provider_name=provider_name,
            dropped_tool_message_count=dropped_orphan_tools,
        )

    if dropped_orphan_tools or dropped_incomplete_groups:
        logger.debug(
            "provider.tool_transcript.sanitized",
            provider_name=provider_name,
            original_message_count=len(messages),
            sanitized_message_count=len(sanitized),
            dropped_orphan_tool_count=dropped_orphan_tools,
            dropped_incomplete_group_count=dropped_incomplete_groups,
            original_roles=[message.role for message in messages],
            sanitized_roles=[message.role for message in sanitized],
        )

    return sanitized


__all__ = ["sanitize_tool_transcript"]
