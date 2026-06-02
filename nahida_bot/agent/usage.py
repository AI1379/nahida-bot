"""Token usage recorder with SQLite persistence and cost estimation.

Records per-request token usage from provider responses and persists
to SQLite so data survives restarts. Falls back to heuristic estimation
when the provider does not return structured usage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from nahida_bot.agent.tokenization import HeuristicTokenizer

if TYPE_CHECKING:
    from nahida_bot.agent.providers.base import TokenUsage
    from nahida_bot.db.engine import DatabaseEngine

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing table (USD per 1 000 tokens)
# ---------------------------------------------------------------------------

_PRICING: dict[str, tuple[float, float]] = {
    # (input_price_per_1k, output_price_per_1k)
    # Anthropic
    "claude-opus-4": (0.015, 0.075),
    "claude-opus-4-8": (0.015, 0.075),
    "claude-sonnet-4": (0.003, 0.015),
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-haiku-4-5": (0.001, 0.005),
    # OpenAI
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4.1": (0.002, 0.008),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1-nano": (0.0001, 0.0004),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5-turbo": (0.0015, 0.002),
    "o3": (0.01, 0.04),
    "o4-mini": (0.0011, 0.0044),
    # DeepSeek
    "deepseek-chat": (0.00027, 0.0011),
    "deepseek-reasoner": (0.00055, 0.00219),
    # Groq
    "llama-3.3-70b": (0.00059, 0.00079),
    "llama-4-scout": (0.00013, 0.0004),
    "mixtral-8x7b": (0.00024, 0.00024),
    "gemma2-9b-it": (0.0002, 0.0002),
    # GLM
    "glm-4": (0.004, 0.004),
    "glm-4-flash": (0.0006, 0.0006),
    # Minimax
    "minimax-m1": (0.004, 0.015),
    "minimax-m2": (0.002, 0.008),
}


def _resolve_pricing(model: str) -> tuple[float, float] | None:
    """Look up (input_price_per_1k, output_price_per_1k) for *model*.

    Tries exact match first, then longest prefix match across known keys.
    """
    if not model:
        return None
    key = model.lower().strip()
    if key in _PRICING:
        return _PRICING[key]
    # Prefix match — longest wins
    best: tuple[str, tuple[float, float]] | None = None
    for k, v in _PRICING.items():
        if key.startswith(k):
            if best is None or len(k) > len(best[0]):
                best = (k, v)
    return best[1] if best else None


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float | None:
    """Estimate cost in USD from token counts and pricing table.

    Cached input tokens are treated as free (prompt cache discount).
    Returns None when no pricing data is available for the model.
    """
    pricing = _resolve_pricing(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    billable_input = max(0, input_tokens - cached_tokens)
    cost = (billable_input / 1000) * input_price
    cost += (output_tokens / 1000) * output_price
    return round(cost, 8)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class UsageEvent:
    """A single usage event recorded from a provider call."""

    id: int | None = None
    timestamp: str = ""
    session_id: str = ""
    source_tag: str = ""
    provider_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated: bool = False
    estimated_cost: float | None = None


@dataclass(slots=True)
class UsageTotals:
    """Aggregate token usage totals."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost: float | None = None
    event_count: int = 0


@dataclass(slots=True)
class ProviderUsageSummary:
    """Per-provider token usage."""

    provider_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated: bool = False
    estimated_cost: float | None = None
    event_count: int = 0


@dataclass(slots=True)
class DailyUsage:
    """Token usage for a single day."""

    date: str  # "YYYY-MM-DD"
    input_tokens: int = 0
    output_tokens: int = 0
    provider_id: str = ""
    model: str = ""


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class UsageRecorder:
    """Records token usage events with in-memory caching and SQLite persistence.

    Lifecycle:
    1. ``__init__()`` — create in-memory store.
    2. ``load_from_db()`` — restore from SQLite on startup.
    3. ``record()`` — write event to memory + SQLite during operation.
    4. ``close()`` — final flush on shutdown.
    """

    def __init__(self, *, max_events: int = 10000) -> None:
        self._events: list[UsageEvent] = []
        self._totals = UsageTotals()
        self._max_events = max_events
        self._db: DatabaseEngine | None = None
        self._heuristic = HeuristicTokenizer()

    # -- DB binding ----------------------------------------------------------

    def bind_db(self, db: DatabaseEngine) -> None:
        """Bind a database engine for persistence."""
        self._db = db

    async def load_from_db(self, db: DatabaseEngine) -> None:
        """Restore in-memory state from SQLite on startup."""
        self._db = db
        try:
            rows = await db.fetch_all(
                "SELECT id, timestamp, session_id, source_tag, provider_id, model, "
                "input_tokens, output_tokens, cached_tokens, reasoning_tokens, "
                "cache_creation_tokens, estimated, estimated_cost "
                "FROM usage_events ORDER BY id DESC LIMIT ?",
                (self._max_events,),
            )
            events: list[UsageEvent] = []
            totals = UsageTotals()
            for row in reversed(rows):
                ev = UsageEvent(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    session_id=row["session_id"],
                    source_tag=row["source_tag"],
                    provider_id=row["provider_id"],
                    model=row["model"],
                    input_tokens=row["input_tokens"],
                    output_tokens=row["output_tokens"],
                    cached_tokens=row["cached_tokens"],
                    reasoning_tokens=row["reasoning_tokens"],
                    cache_creation_tokens=row["cache_creation_tokens"],
                    estimated=bool(row["estimated"]),
                    estimated_cost=row["estimated_cost"],
                )
                events.append(ev)
                totals.input_tokens += ev.input_tokens
                totals.output_tokens += ev.output_tokens
                totals.cached_tokens += ev.cached_tokens
                totals.reasoning_tokens += ev.reasoning_tokens
                totals.cache_creation_tokens += ev.cache_creation_tokens
                if ev.estimated_cost is not None:
                    totals.estimated_cost = (
                        totals.estimated_cost or 0
                    ) + ev.estimated_cost
                totals.event_count += 1
            self._events = events
            self._totals = totals
            logger.info(
                "usage_recorder.db_loaded",
                event_count=totals.event_count,
                input_tokens=totals.input_tokens,
            )
        except Exception:
            logger.warning("usage_recorder.db_load_failed", exc_info=True)

    # -- Recording -----------------------------------------------------------

    async def record(
        self,
        *,
        provider_id: str = "",
        model: str = "",
        session_id: str = "",
        source_tag: str = "",
        usage: TokenUsage | None = None,
        estimated: bool = False,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> UsageEvent:
        """Record a usage event.

        When *usage* is provided its fields are used.  Otherwise
        *input_tokens* / *output_tokens* set the raw counts directly.
        *estimated* must be set True when using heuristics rather than
        provider-reported numbers.
        """
        if usage is not None:
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            cached = usage.cached_tokens
            reasoning = usage.reasoning_tokens
            cache_creation = usage.cache_creation_tokens
        else:
            cached = 0
            reasoning = 0
            cache_creation = 0

        cost = (
            None
            if estimated
            else _estimate_cost(model, input_tokens, output_tokens, cached)
        )

        event = UsageEvent(
            timestamp=datetime.now(UTC).isoformat(),
            session_id=session_id,
            source_tag=source_tag,
            provider_id=provider_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached,
            reasoning_tokens=reasoning,
            cache_creation_tokens=cache_creation,
            estimated=estimated,
            estimated_cost=cost,
        )

        # In-memory
        self._events.append(event)
        if len(self._events) > self._max_events:
            dropped = len(self._events) - self._max_events
            self._events = self._events[-self._max_events :]
            logger.debug("usage_recorder.cache_trimmed", dropped=dropped)

        t = self._totals
        t.input_tokens += input_tokens
        t.output_tokens += output_tokens
        t.cached_tokens += cached
        t.reasoning_tokens += reasoning
        t.cache_creation_tokens += cache_creation
        if cost is not None:
            t.estimated_cost = (t.estimated_cost or 0) + cost
        t.event_count += 1

        # Persist
        await self._insert_event(event)

        return event

    async def _insert_event(self, event: UsageEvent) -> None:
        """Persist a usage event to SQLite."""
        if self._db is None:
            return
        try:
            async with self._db.write_lock:
                await self._db.execute(
                    "INSERT INTO usage_events "
                    "(timestamp, session_id, source_tag, provider_id, model, "
                    "input_tokens, output_tokens, cached_tokens, reasoning_tokens, "
                    "cache_creation_tokens, estimated, estimated_cost) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.timestamp,
                        event.session_id,
                        event.source_tag,
                        event.provider_id,
                        event.model,
                        event.input_tokens,
                        event.output_tokens,
                        event.cached_tokens,
                        event.reasoning_tokens,
                        event.cache_creation_tokens,
                        1 if event.estimated else 0,
                        event.estimated_cost,
                    ),
                )
                await self._db.db.commit()
        except Exception:
            logger.warning("usage_recorder.db_insert_failed", exc_info=True)

    async def estimate_and_record(
        self,
        *,
        provider_id: str,
        model: str,
        session_id: str = "",
        source_tag: str = "",
        prompt_text: str = "",
        output_text: str = "",
    ) -> UsageEvent:
        """Fallback: estimate tokens with heuristic tokenizer and record."""
        input_est = self._heuristic.count_tokens(prompt_text) if prompt_text else 0
        output_est = self._heuristic.count_tokens(output_text) if output_text else 0
        if input_est == 0 and output_est == 0:
            # Last resort: character estimate
            input_est = max(1, math.ceil(len(prompt_text) / 4)) if prompt_text else 0
            output_est = max(1, math.ceil(len(output_text) / 4)) if output_text else 0
        return await self.record(
            provider_id=provider_id,
            model=model,
            session_id=session_id,
            source_tag=source_tag,
            estimated=True,
            input_tokens=input_est,
            output_tokens=output_est,
        )

    # -- Queries -------------------------------------------------------------

    def get_totals(
        self,
        *,
        provider_id: str | None = None,
        since: datetime | None = None,
    ) -> UsageTotals:
        """Return aggregate totals, optionally filtered."""
        if provider_id is None and since is None:
            return UsageTotals(
                input_tokens=self._totals.input_tokens,
                output_tokens=self._totals.output_tokens,
                cached_tokens=self._totals.cached_tokens,
                reasoning_tokens=self._totals.reasoning_tokens,
                cache_creation_tokens=self._totals.cache_creation_tokens,
                estimated_cost=self._totals.estimated_cost,
                event_count=self._totals.event_count,
            )
        # Filtered from in-memory events
        totals = UsageTotals()
        for ev in self._events:
            if provider_id and ev.provider_id != provider_id:
                continue
            if since and ev.timestamp < since.isoformat():
                continue
            totals.input_tokens += ev.input_tokens
            totals.output_tokens += ev.output_tokens
            totals.cached_tokens += ev.cached_tokens
            totals.reasoning_tokens += ev.reasoning_tokens
            totals.cache_creation_tokens += ev.cache_creation_tokens
            if ev.estimated_cost is not None:
                totals.estimated_cost = (totals.estimated_cost or 0) + ev.estimated_cost
            totals.event_count += 1
        return totals

    def get_recent(
        self,
        *,
        limit: int = 100,
        provider_id: str | None = None,
        since: datetime | None = None,
    ) -> list[UsageEvent]:
        """Return recent events, newest last."""
        filtered = self._events
        if provider_id:
            filtered = [e for e in filtered if e.provider_id == provider_id]
        if since:
            since_iso = since.isoformat()
            filtered = [e for e in filtered if e.timestamp >= since_iso]
        return filtered[-limit:]

    def get_by_provider(
        self,
        *,
        since: datetime | None = None,
    ) -> list[ProviderUsageSummary]:
        """Return usage aggregated by (provider_id, model)."""
        groups: dict[tuple[str, str], ProviderUsageSummary] = {}
        for ev in self._events:
            if since and ev.timestamp < since.isoformat():
                continue
            key = (ev.provider_id, ev.model)
            if key not in groups:
                groups[key] = ProviderUsageSummary(
                    provider_id=ev.provider_id,
                    model=ev.model,
                    estimated=ev.estimated,
                )
            s = groups[key]
            s.input_tokens += ev.input_tokens
            s.output_tokens += ev.output_tokens
            s.cached_tokens += ev.cached_tokens
            s.reasoning_tokens += ev.reasoning_tokens
            s.cache_creation_tokens += ev.cache_creation_tokens
            if ev.estimated_cost is not None:
                s.estimated_cost = (s.estimated_cost or 0) + ev.estimated_cost
            s.event_count += 1
            # A group is only estimated if *all* its events are
            if not ev.estimated:
                s.estimated = False
        return sorted(
            groups.values(),
            key=lambda s: s.input_tokens + s.output_tokens,
            reverse=True,
        )

    def get_daily_breakdown(
        self,
        *,
        days: int = 7,
        provider_id: str | None = None,
    ) -> list[DailyUsage]:
        """Return daily token counts for the last *days* days."""
        today = date.today()
        buckets: dict[str, DailyUsage] = {}
        cutoff = today - timedelta(days=days)
        for ev in self._events:
            if provider_id and ev.provider_id != provider_id:
                continue
            try:
                ev_date = datetime.fromisoformat(ev.timestamp).date()
            except (ValueError, TypeError):
                continue
            if ev_date < cutoff:
                continue
            dkey = ev_date.isoformat()
            if dkey not in buckets:
                buckets[dkey] = DailyUsage(
                    date=dkey,
                    provider_id=ev.provider_id,
                    model=ev.model,
                )
            buckets[dkey].input_tokens += ev.input_tokens
            buckets[dkey].output_tokens += ev.output_tokens
        result = sorted(buckets.values(), key=lambda d: d.date)
        return result

    async def get_events_from_db(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        provider_id: str | None = None,
        since: datetime | None = None,
    ) -> list[UsageEvent]:
        """Query usage events directly from SQLite (for paginated access)."""
        if self._db is None:
            return self.get_recent(limit=limit, provider_id=provider_id, since=since)
        try:
            conditions = []
            params: list[object] = []
            if provider_id:
                conditions.append("provider_id = ?")
                params.append(provider_id)
            if since:
                conditions.append("timestamp >= ?")
                params.append(since.isoformat())
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.extend([limit, offset])
            rows = await self._db.fetch_all(
                f"SELECT id, timestamp, session_id, source_tag, provider_id, model, "
                f"input_tokens, output_tokens, cached_tokens, reasoning_tokens, "
                f"cache_creation_tokens, estimated, estimated_cost "
                f"FROM usage_events {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                tuple(params),
            )
            events: list[UsageEvent] = []
            for row in reversed(rows):
                events.append(
                    UsageEvent(
                        id=row["id"],
                        timestamp=row["timestamp"],
                        session_id=row["session_id"],
                        source_tag=row["source_tag"],
                        provider_id=row["provider_id"],
                        model=row["model"],
                        input_tokens=row["input_tokens"],
                        output_tokens=row["output_tokens"],
                        cached_tokens=row["cached_tokens"],
                        reasoning_tokens=row["reasoning_tokens"],
                        cache_creation_tokens=row["cache_creation_tokens"],
                        estimated=bool(row["estimated"]),
                        estimated_cost=row["estimated_cost"],
                    )
                )
            return events
        except Exception:
            logger.warning("usage_recorder.db_query_failed", exc_info=True)
            return []

    # -- Administration ------------------------------------------------------

    async def clear(self) -> None:
        """Clear all usage events from memory and database."""
        self._events.clear()
        self._totals = UsageTotals()
        if self._db is not None:
            try:
                async with self._db.write_lock:
                    await self._db.execute("DELETE FROM usage_events")
                    await self._db.db.commit()
            except Exception:
                logger.warning("usage_recorder.db_clear_failed", exc_info=True)

    async def close(self) -> None:
        """Flush and release resources.  No-op if no DB bound."""
        pass  # SQLite writes are synchronous; nothing to flush
