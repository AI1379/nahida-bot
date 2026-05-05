"""Tests for multimodal metrics: media resolve, fallback, cache usage."""

from __future__ import annotations

from nahida_bot.agent.metrics import MetricsCollector


class TestMediaMetrics:
    def test_record_media_resolve(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_media_resolve(
            trace,
            media_id="img_1",
            source="url",
            latency_seconds=0.5,
        )
        collector.record_media_resolve(
            trace,
            media_id="img_2",
            source="cache_hit",
            latency_seconds=0.01,
        )
        assert len(trace.media_resolves) == 2
        assert trace.media_resolves[0].source == "url"
        assert trace.media_resolves[1].source == "cache_hit"

    def test_media_resolve_stats(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_media_resolve(
            trace, media_id="a", source="url", latency_seconds=1.0
        )
        collector.record_media_resolve(
            trace, media_id="b", source="cache_hit", latency_seconds=0.1
        )
        collector.record_media_resolve(
            trace, media_id="c", source="cache_hit", latency_seconds=0.2
        )

        stats = collector.media_resolve_stats()
        assert stats["count"] == 3.0
        assert abs(stats["cache_hit_rate"] - 2 / 3) < 0.01
        assert abs(stats["avg_latency"] - (1.0 + 0.1 + 0.2) / 3) < 0.01

    def test_media_resolve_stats_empty(self) -> None:
        collector = MetricsCollector()
        stats = collector.media_resolve_stats()
        assert stats["count"] == 0.0

    def test_record_image_fallback(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_image_fallback(
            trace,
            step=1,
            provider_id="vision-provider",
            model="gpt-4o",
            latency_seconds=2.0,
            success=True,
        )
        assert len(trace.image_fallbacks) == 1
        assert trace.image_fallbacks[0].provider_id == "vision-provider"

    def test_record_cache_usage(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_cache_usage(
            trace, step=1, cached_tokens=500, total_input_tokens=1000
        )
        collector.record_cache_usage(
            trace, step=2, cached_tokens=300, total_input_tokens=800
        )
        assert len(trace.cache_usage) == 2

    def test_cache_hit_rate(self) -> None:
        collector = MetricsCollector()
        trace = collector.new_trace()
        collector.record_cache_usage(
            trace, step=1, cached_tokens=500, total_input_tokens=1000
        )
        collector.record_cache_usage(
            trace, step=2, cached_tokens=300, total_input_tokens=800
        )
        rate = collector.cache_hit_rate()
        assert abs(rate - 800 / 1800) < 0.01

    def test_cache_hit_rate_no_data(self) -> None:
        collector = MetricsCollector()
        assert collector.cache_hit_rate() == 0.0
