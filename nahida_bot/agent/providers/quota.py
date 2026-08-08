"""Provider-reported quota models and provider-specific query adapters."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

QuotaErrorKind = Literal["unsupported", "auth", "transient", "request", "parse"]


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    """One provider-reported quota or balance window."""

    name: str
    percent_remaining: float | None = None
    used: float | None = None
    limit: float | None = None
    unit: str | None = None
    reset_at: str | None = None


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Normalized quota data returned by one configured provider account."""

    provider_id: str
    provider_label: str
    adapter: str
    plan_name: str | None
    windows: tuple[QuotaWindow, ...]
    queried_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["windows"] = [asdict(window) for window in self.windows]
        return data


@dataclass(frozen=True, slots=True)
class QuotaReport:
    """One successful or failed provider quota query."""

    provider_id: str
    snapshot: QuotaSnapshot | None = None
    error: str | None = None
    error_kind: QuotaErrorKind | None = None
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
            "error": self.error,
            "error_kind": self.error_kind,
            "cached": self.cached,
        }


class QuotaQueryError(RuntimeError):
    """A safe-to-display quota query error without response credentials."""

    def __init__(self, message: str, kind: QuotaErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _path(root: object, parts: object) -> object:
    if not isinstance(parts, list):
        return None
    current = root
    for part in parts:
        if not isinstance(part, str) or not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _iso_timestamp(value: object) -> str | None:
    if isinstance(value, str):
        return value
    number = _number(value)
    if number is None or number <= 0:
        return None
    seconds = number / 1000 if number >= 1_000_000_000_000 else number
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _config_value(provider: Any, config: dict[str, Any], key: str) -> str:
    configured = config.get(key)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    value = getattr(provider, key, "")
    return value.strip() if isinstance(value, str) else ""


async def _get_json(provider: Any, url: str, *, headers: dict[str, str]) -> Any:
    """Perform a bounded provider query with stable error categories."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    is_loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise QuotaQueryError("Quota endpoint must use HTTPS", "request")
    if parsed.username or parsed.password or not hostname:
        raise QuotaQueryError("Quota endpoint is invalid", "request")
    client = provider._ensure_client()  # Provider-owned client/lifecycle.
    try:
        response = await client.get(url, headers=headers, timeout=15.0)
    except httpx.TimeoutException as exc:
        raise QuotaQueryError("Quota request timed out", "transient") from exc
    except httpx.HTTPError as exc:
        raise QuotaQueryError("Quota network request failed", "transient") from exc

    if 300 <= response.status_code < 400:
        raise QuotaQueryError("Quota endpoint redirects are not allowed", "request")
    if response.status_code in (401, 403):
        raise QuotaQueryError(
            f"Quota authentication failed (HTTP {response.status_code})", "auth"
        )
    if response.status_code == 429 or response.status_code >= 500:
        raise QuotaQueryError(
            f"Quota service temporarily unavailable (HTTP {response.status_code})",
            "transient",
        )
    if response.status_code >= 400:
        raise QuotaQueryError(
            f"Quota request failed (HTTP {response.status_code})", "request"
        )

    raw = response.content
    if len(raw) > 256 * 1024:
        raise QuotaQueryError("Quota response is too large", "parse")
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise QuotaQueryError("Quota response was not valid JSON", "parse") from exc


def _bearer_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _require_key(provider: Any, config: dict[str, Any]) -> str:
    key = _config_value(provider, config, "api_key")
    if not key:
        raise QuotaQueryError("Provider API key is not configured", "auth")
    return key


def _snapshot(
    provider_id: str,
    provider: Any,
    adapter: str,
    windows: list[QuotaWindow],
    *,
    plan_name: str | None = None,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider_id=provider_id,
        provider_label=str(getattr(provider, "name", provider_id)),
        adapter=adapter,
        plan_name=plan_name,
        windows=tuple(windows),
        queried_at=_now_iso(),
    )


async def _query_deepseek(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    body = await _get_json(
        provider,
        str(config.get("url") or "https://api.deepseek.com/user/balance"),
        headers=_bearer_headers(key),
    )
    if not isinstance(body, dict):
        raise QuotaQueryError("DeepSeek quota response has an invalid shape", "parse")
    available = body.get("is_available", True) is not False
    windows: list[QuotaWindow] = []
    infos = body.get("balance_infos")
    if isinstance(infos, list):
        for info in infos:
            if not isinstance(info, dict):
                continue
            currency = str(info.get("currency") or "CNY")
            windows.append(
                QuotaWindow(
                    name=f"Balance ({currency})",
                    percent_remaining=100.0 if available else 0.0,
                    limit=_number(info.get("total_balance")),
                    unit=currency,
                )
            )
    if not windows:
        raise QuotaQueryError("DeepSeek returned no balance information", "parse")
    return _snapshot(provider_id, provider, "deepseek-balance", windows)


def _minimax_model(body: dict[str, Any]) -> dict[str, Any] | None:
    models = body.get("model_remains")
    if not isinstance(models, list):
        return None
    candidates = [
        item
        for item in models
        if isinstance(item, dict)
        and str(item.get("model_name", "")).lower() in {"general", "minimax-m*"}
    ]
    return candidates[0] if candidates else None


async def _query_minimax(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    base_url = str(getattr(provider, "base_url", ""))
    default_host = "api.minimax.io" if "minimax.io" in base_url else "api.minimaxi.com"
    url = (
        config.get("url")
        or f"https://{default_host}/v1/api/openplatform/coding_plan/remains"
    )
    body = await _get_json(provider, str(url), headers=_bearer_headers(key))
    if not isinstance(body, dict):
        raise QuotaQueryError("MiniMax quota response has an invalid shape", "parse")
    base_resp = body.get("base_resp")
    if isinstance(base_resp, dict) and _number(base_resp.get("status_code")) not in (
        None,
        0.0,
    ):
        raise QuotaQueryError("MiniMax rejected the quota request", "request")
    model = _minimax_model(body)
    if model is None:
        raise QuotaQueryError("MiniMax returned no coding-plan quota", "parse")
    windows: list[QuotaWindow] = []
    for name, remaining_key, reset_key in (
        ("5h", "current_interval_remaining_percent", "end_time"),
        ("Weekly", "current_weekly_remaining_percent", "weekly_end_time"),
    ):
        remaining = _number(model.get(remaining_key))
        if remaining is None:
            continue
        if name == "Weekly" and _number(model.get("current_weekly_status")) not in (
            None,
            1.0,
        ):
            continue
        windows.append(
            QuotaWindow(
                name=name,
                percent_remaining=max(0.0, min(100.0, remaining)),
                reset_at=_iso_timestamp(model.get(reset_key)),
            )
        )
    if not windows:
        raise QuotaQueryError("MiniMax returned no reportable quota windows", "parse")
    return _snapshot(provider_id, provider, "minimax-coding-plan", windows)


async def _query_kimi(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    body = await _get_json(
        provider,
        str(config.get("url") or "https://api.kimi.com/coding/v1/usages"),
        headers=_bearer_headers(key),
    )
    if not isinstance(body, dict):
        raise QuotaQueryError("Kimi quota response has an invalid shape", "parse")
    windows: list[QuotaWindow] = []
    limits = body.get("limits")
    if isinstance(limits, list):
        for item in limits:
            detail = item.get("detail") if isinstance(item, dict) else None
            if not isinstance(detail, dict):
                continue
            limit = _number(detail.get("limit"))
            remaining = _number(detail.get("remaining"))
            if limit is None or remaining is None or limit <= 0:
                continue
            windows.append(
                QuotaWindow(
                    name="5h",
                    percent_remaining=max(0.0, min(100.0, remaining / limit * 100)),
                    used=max(0.0, limit - remaining),
                    limit=limit,
                    reset_at=_iso_timestamp(detail.get("resetTime")),
                )
            )
    usage = body.get("usage")
    if isinstance(usage, dict):
        limit = _number(usage.get("limit"))
        remaining = _number(usage.get("remaining"))
        if limit is not None and remaining is not None and limit > 0:
            windows.append(
                QuotaWindow(
                    name="Weekly",
                    percent_remaining=max(0.0, min(100.0, remaining / limit * 100)),
                    used=max(0.0, limit - remaining),
                    limit=limit,
                    reset_at=_iso_timestamp(usage.get("resetTime")),
                )
            )
    if not windows:
        raise QuotaQueryError("Kimi returned no reportable quota windows", "parse")
    return _snapshot(provider_id, provider, "kimi-coding-plan", windows)


async def _query_zhipu(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    base_url = str(getattr(provider, "base_url", "")).lower()
    quota_host = "api.z.ai" if "api.z.ai" in base_url else "open.bigmodel.cn"
    # Team plans are currently exposed only by the mainland endpoint.
    if config.get("team") is True:
        quota_host = "open.bigmodel.cn"
    url = str(
        config.get("url") or f"https://{quota_host}/api/monitor/usage/quota/limit"
    )
    if config.get("team") is True and "?" not in url:
        url += "?type=2"
    headers = {"Authorization": key, "Accept": "application/json"}
    for config_key, header_name in (
        ("organization_id", "bigmodel-organization"),
        ("project_id", "bigmodel-project"),
    ):
        value = config.get(config_key)
        if isinstance(value, str) and value.strip():
            headers[header_name] = value.strip()
    body = await _get_json(provider, url, headers=headers)
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise QuotaQueryError("Zhipu quota response has an invalid shape", "parse")
    if body.get("success") is False:
        raise QuotaQueryError("Zhipu rejected the quota request", "request")
    data = body["data"]
    windows: list[QuotaWindow] = []
    limits = data.get("limits")
    for item in limits if isinstance(limits, list) else []:
        if (
            not isinstance(item, dict)
            or str(item.get("type", "")).upper() != "TOKENS_LIMIT"
        ):
            continue
        unit = _number(item.get("unit"))
        name = "5h" if unit == 3 else "Weekly" if unit == 6 else "Quota"
        used = _number(item.get("percentage"))
        if used is None:
            continue
        windows.append(
            QuotaWindow(
                name=name,
                percent_remaining=max(0.0, min(100.0, 100.0 - used)),
                reset_at=_iso_timestamp(item.get("nextResetTime")),
            )
        )
    if not windows:
        raise QuotaQueryError("Zhipu returned no reportable quota windows", "parse")
    return _snapshot(
        provider_id,
        provider,
        "zhipu-coding-plan",
        windows,
        plan_name=str(data.get("level") or "") or None,
    )


async def _query_siliconflow(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    body = await _get_json(
        provider,
        str(config.get("url") or "https://api.siliconflow.cn/v1/user/info"),
        headers=_bearer_headers(key),
    )
    data = body.get("data") if isinstance(body, dict) else None
    remaining = _number(data.get("totalBalance")) if isinstance(data, dict) else None
    if remaining is None:
        raise QuotaQueryError("SiliconFlow returned no balance", "parse")
    unit = (
        "USD" if "siliconflow.com" in str(getattr(provider, "base_url", "")) else "CNY"
    )
    return _snapshot(
        provider_id,
        provider,
        "siliconflow-balance",
        [QuotaWindow(name="Balance", limit=remaining, unit=unit)],
    )


async def _query_openrouter(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    body = await _get_json(
        provider,
        str(config.get("url") or "https://openrouter.ai/api/v1/credits"),
        headers=_bearer_headers(key),
    )
    data = body.get("data", body) if isinstance(body, dict) else {}
    total = _number(data.get("total_credits")) if isinstance(data, dict) else None
    used = _number(data.get("total_usage")) if isinstance(data, dict) else None
    if total is None or used is None or total <= 0:
        raise QuotaQueryError("OpenRouter returned no credit information", "parse")
    return _snapshot(
        provider_id,
        provider,
        "openrouter-balance",
        [
            QuotaWindow(
                name="Balance",
                percent_remaining=max(0.0, (total - used) / total * 100),
                used=used,
                limit=total,
                unit="USD",
            )
        ],
    )


async def _query_json_v1(
    provider_id: str, provider: Any, config: dict[str, Any]
) -> QuotaSnapshot:
    key = _require_key(provider, config)
    url = config.get("url")
    mappings = config.get("windows")
    if not isinstance(url, str) or not url.strip() or not isinstance(mappings, list):
        raise QuotaQueryError(
            "json-v1 quota adapter requires url and windows", "request"
        )
    body = await _get_json(provider, url, headers=_bearer_headers(key))
    windows: list[QuotaWindow] = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        name = str(mapping.get("name") or "Quota")
        remaining = _number(_path(body, mapping.get("remaining_path")))
        used = _number(_path(body, mapping.get("used_path")))
        limit = _number(_path(body, mapping.get("limit_path")))
        if remaining is None and used is not None and limit is not None:
            remaining = limit - used
        if remaining is None and used is None and limit is None:
            continue
        percent = None if remaining is None or not limit else remaining / limit * 100
        windows.append(
            QuotaWindow(
                name=name,
                percent_remaining=percent,
                used=used,
                limit=limit,
                unit=str(mapping.get("unit")) if mapping.get("unit") else None,
                reset_at=_iso_timestamp(_path(body, mapping.get("reset_path"))),
            )
        )
    if not windows:
        raise QuotaQueryError("json-v1 returned no reportable quota windows", "parse")
    return _snapshot(provider_id, provider, "json-v1", windows)


_DEFAULT_ADAPTERS = {
    "deepseek": "deepseek-balance",
    "minimax": "minimax-coding-plan",
    "glm": "zhipu-coding-plan",
}


async def query_configured_quota(
    provider_id: str,
    provider: Any,
    config: dict[str, Any] | None,
) -> QuotaSnapshot:
    """Dispatch a provider-owned quota adapter."""
    values = config or {}
    adapter = str(
        values.get("adapter") or _DEFAULT_ADAPTERS.get(provider.name, "")
    ).lower()
    if not adapter or adapter in {"none", "disabled"}:
        raise QuotaQueryError("Provider quota is not configured", "unsupported")
    handlers = {
        "deepseek-balance": _query_deepseek,
        "siliconflow-balance": _query_siliconflow,
        "openrouter-balance": _query_openrouter,
        "minimax-coding-plan": _query_minimax,
        "kimi-coding-plan": _query_kimi,
        "zhipu-coding-plan": _query_zhipu,
        "json-v1": _query_json_v1,
    }
    handler = handlers.get(adapter)
    if handler is None:
        raise QuotaQueryError(f"Unknown quota adapter: {adapter}", "unsupported")
    return await handler(provider_id, provider, values)
