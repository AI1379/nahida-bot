"""Tests for provider-owned quota adapters."""

from __future__ import annotations

import httpx
import pytest

from nahida_bot.agent.context import ContextBuilder
from nahida_bot.agent.providers.manager import ProviderManager, ProviderSlot
from nahida_bot.agent.providers.quota import QuotaQueryError, query_configured_quota
from nahida_bot.agent.providers.quota import QuotaSnapshot, QuotaWindow


class _FakeProvider:
    name = "deepseek"
    api_key = "sk-test"
    base_url = "https://api.deepseek.com"

    def __init__(self, handler):
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _ensure_client(self) -> httpx.AsyncClient:
        return self._client


class _CountingQuotaProvider:
    calls = 0

    async def query_quota(self, *, provider_id: str) -> QuotaSnapshot:
        self.calls += 1
        return QuotaSnapshot(
            provider_id=provider_id,
            provider_label="Test",
            adapter="test",
            plan_name=None,
            windows=(QuotaWindow(name="5h", percent_remaining=75),),
            queried_at="2026-08-06T00:00:00+00:00",
        )


@pytest.mark.asyncio
async def test_deepseek_balance_is_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/balance"
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [
                    {"currency": "CNY", "total_balance": "12.5"},
                ],
            },
        )

    provider = _FakeProvider(handler)
    result = await query_configured_quota("deepseek-main", provider, {})
    await provider._client.aclose()

    assert result.adapter == "deepseek-balance"
    assert result.windows[0].limit == 12.5
    assert result.windows[0].unit == "CNY"
    assert isinstance(result.to_dict()["windows"], list)


@pytest.mark.asyncio
async def test_custom_json_adapter_can_be_used_by_anthropic_relay() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/quota"
        return httpx.Response(
            200,
            json={
                "data": {
                    "five_hour": {
                        "used": 2,
                        "limit": 10,
                        "reset_at": "2026-08-06T12:00:00Z",
                    }
                }
            },
        )

    provider = _FakeProvider(handler)
    provider.name = "anthropic"
    result = await query_configured_quota(
        "relay",
        provider,
        {
            "adapter": "json-v1",
            "url": "https://relay.example/quota",
            "windows": [
                {
                    "name": "5h",
                    "used_path": ["data", "five_hour", "used"],
                    "limit_path": ["data", "five_hour", "limit"],
                    "reset_path": ["data", "five_hour", "reset_at"],
                }
            ],
        },
    )
    await provider._client.aclose()

    assert result.provider_id == "relay"
    assert result.windows[0].percent_remaining == 80
    assert result.windows[0].used == 2


@pytest.mark.asyncio
async def test_zhipu_quota_uses_international_site_for_api_z_ai() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.z.ai"
        assert request.url.path == "/api/monitor/usage/quota/limit"
        assert request.headers["authorization"] == "zhipu-key"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "level": " Coding Plan",
                    "limits": [
                        {
                            "type": "TOKENS_LIMIT",
                            "unit": 3,
                            "percentage": 25,
                        }
                    ],
                },
            },
        )

    provider = _FakeProvider(handler)
    provider.name = "glm"
    provider.api_key = "zhipu-key"
    provider.base_url = "https://api.z.ai/api/anthropic"
    result = await query_configured_quota(
        "zhipu-international",
        provider,
        {"adapter": "zhipu-coding-plan"},
    )
    await provider._client.aclose()

    assert result.windows[0].name == "5h"
    assert result.windows[0].percent_remaining == 75


@pytest.mark.asyncio
async def test_transient_and_auth_errors_are_classified_without_response_body() -> None:
    async def transient_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="secret provider detail")

    provider = _FakeProvider(transient_handler)
    with pytest.raises(QuotaQueryError) as transient:
        await query_configured_quota("deepseek", provider, {})
    await provider._client.aclose()
    assert transient.value.kind == "transient"
    assert "secret" not in str(transient.value)

    async def auth_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret token detail")

    provider = _FakeProvider(auth_handler)
    with pytest.raises(QuotaQueryError) as auth:
        await query_configured_quota("deepseek", provider, {})
    await provider._client.aclose()
    assert auth.value.kind == "auth"
    assert "secret" not in str(auth.value)


@pytest.mark.asyncio
async def test_provider_manager_coalesces_short_lived_quota_results() -> None:
    provider = _CountingQuotaProvider()
    manager = ProviderManager(
        [
            ProviderSlot(
                id="test",
                provider=provider,  # type: ignore[arg-type]
                context_builder=ContextBuilder(),
                default_model="test-model",
            )
        ]
    )

    first = await manager.query_quotas("test")
    second = await manager.query_quotas("test")

    assert first[0].snapshot is not None
    assert second[0].cached is True
    assert provider.calls == 1
