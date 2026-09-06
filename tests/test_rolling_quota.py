"""Tests for the shared rolling quota reservation primitive."""

import pytest

from nahida_bot.plugins.rolling_quota import (
    RollingQuotaExceeded,
    RollingQuotaLimiter,
)


@pytest.mark.asyncio
async def test_reservation_release_and_multi_unit_retry_after(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr("nahida_bot.plugins.rolling_quota.time.time", lambda: now)
    limiter = RollingQuotaLimiter(limit=3, window_seconds=10)

    first = await limiter.reserve()
    now = 103.0
    second = await limiter.reserve()
    now = 105.0
    third = await limiter.reserve()
    assert first is not None and second is not None and third is not None

    with pytest.raises(RollingQuotaExceeded) as exc_info:
        await limiter.reserve(2)
    # Two units must expire before a two-unit request fits again.
    assert exc_info.value.retry_after == 8.0

    await limiter.release(first)
    fourth = await limiter.reserve(1)
    assert fourth is not None
