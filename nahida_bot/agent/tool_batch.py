"""Run declared-safe tool segments with bounded fan-out and ordered results."""

from __future__ import annotations

import asyncio
from collections import Counter, deque
from typing import Awaitable, Callable, TypeVar, cast

from nahida_bot.agent.providers import ToolCall

_Result = TypeVar("_Result")


async def _parallel_segment(
    calls: list[ToolCall],
    execute: Callable[[ToolCall], Awaitable[_Result]],
    limit: int,
) -> list[_Result]:
    pending = deque(enumerate(calls))
    results: list[_Result | None] = [None] * len(calls)

    async def worker() -> None:
        while pending:
            index, call = pending.popleft()
            results[index] = await execute(call)

    workers = [asyncio.create_task(worker()) for _ in range(min(limit, len(calls)))]
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    return cast(list[_Result], results)


async def execute_tool_batch(
    calls: list[ToolCall],
    *,
    execute: Callable[[ToolCall], Awaitable[_Result]],
    parallel_safe: Callable[[ToolCall], bool],
    limit: int,
) -> list[_Result]:
    """Unsafe calls are barriers; duplicate call IDs never overlap."""
    counts = Counter(call.call_id for call in calls)
    results: list[_Result] = []
    segment: list[ToolCall] = []
    for call in calls:
        if limit > 1 and counts[call.call_id] == 1 and parallel_safe(call):
            segment.append(call)
            continue
        if segment:
            results.extend(await _parallel_segment(segment, execute, limit))
            segment.clear()
        results.append(await execute(call))
    if segment:
        results.extend(await _parallel_segment(segment, execute, limit))
    return results
