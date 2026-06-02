"""Token usage API endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from nahida_bot.gateway.deps import get_application
from nahida_bot.gateway.schemas import (
    DailyTokenSchema,
    ProviderTokenSchema,
    TokenClearResponse,
    TokenEventsResponse,
    TokenStatsResponse,
    TokenTotalsSchema,
)

router = APIRouter()


@router.get("/api/tokens/stats", response_model=TokenStatsResponse)
async def get_token_stats(
    provider_id: str = Query("", description="Filter by provider ID"),
    days: int = Query(7, ge=1, le=365, description="Days for daily breakdown"),
    app=Depends(get_application),
) -> TokenStatsResponse:
    """Return aggregate totals, per-provider breakdown, and daily chart data."""
    recorder = app._usage_ledger
    if recorder is None:
        return TokenStatsResponse(
            totals=TokenTotalsSchema(),
            by_provider=[],
            daily=[],
        )

    since = datetime.now().astimezone() - timedelta(days=days) if days > 0 else None
    totals = recorder.get_totals(provider_id=provider_id or None, since=since)
    by_provider_raw = recorder.get_by_provider(since=since)
    daily_raw = recorder.get_daily_breakdown(days=days, provider_id=provider_id or None)

    return TokenStatsResponse(
        totals=TokenTotalsSchema(
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cached_tokens=totals.cached_tokens,
            reasoning_tokens=totals.reasoning_tokens,
            cache_creation_tokens=totals.cache_creation_tokens,
            estimated_cost=totals.estimated_cost,
            event_count=totals.event_count,
        ),
        by_provider=[
            ProviderTokenSchema(
                provider_id=s.provider_id,
                model=s.model,
                input_tokens=s.input_tokens,
                output_tokens=s.output_tokens,
                cached_tokens=s.cached_tokens,
                reasoning_tokens=s.reasoning_tokens,
                cache_creation_tokens=s.cache_creation_tokens,
                estimated=s.estimated,
                estimated_cost=s.estimated_cost,
                event_count=s.event_count,
            )
            for s in by_provider_raw
        ],
        daily=[
            DailyTokenSchema(
                date=d.date,
                input_tokens=d.input_tokens,
                output_tokens=d.output_tokens,
                provider_id=d.provider_id,
                model=d.model,
            )
            for d in daily_raw
        ],
    )


@router.get("/api/tokens/events", response_model=TokenEventsResponse)
async def get_token_events(
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    provider_id: str = Query("", description="Filter by provider ID"),
    since_days: int = Query(0, ge=0, le=365, description="Days back to look"),
    app=Depends(get_application),
) -> TokenEventsResponse:
    """Return recent token usage events."""
    recorder = app._usage_ledger
    if recorder is None:
        return TokenEventsResponse(events=[])

    since = (
        datetime.now().astimezone() - timedelta(days=since_days)
        if since_days > 0
        else None
    )

    events = await recorder.get_events_from_db(
        limit=limit,
        provider_id=provider_id or None,
        since=since,
    )

    from nahida_bot.gateway.schemas import TokenEventSchema

    return TokenEventsResponse(
        events=[
            TokenEventSchema(
                id=ev.id,
                timestamp=ev.timestamp,
                session_id=ev.session_id,
                source_tag=ev.source_tag,
                provider_id=ev.provider_id,
                model=ev.model,
                input_tokens=ev.input_tokens,
                output_tokens=ev.output_tokens,
                cached_tokens=ev.cached_tokens,
                reasoning_tokens=ev.reasoning_tokens,
                cache_creation_tokens=ev.cache_creation_tokens,
                estimated=ev.estimated,
                estimated_cost=ev.estimated_cost,
            )
            for ev in events
        ],
    )


@router.delete("/api/tokens", response_model=TokenClearResponse)
async def clear_tokens(
    confirm: bool = Query(False, description="Must be true to confirm"),
    app=Depends(get_application),
) -> TokenClearResponse:
    """Clear all token usage events. Requires confirm=true."""
    if not confirm:
        return TokenClearResponse(cleared=False)

    recorder = app._usage_ledger
    if recorder is not None:
        await recorder.clear()
        return TokenClearResponse(cleared=True)
    return TokenClearResponse(cleared=False)
