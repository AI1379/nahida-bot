"""Durable notification intent and user-owned routing preferences."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=12000)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Expiry must include a timezone")
        return value


class RoutingPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    desktop_enabled: bool = True
    fallback_enabled: bool = False
    primary_node_id: str = Field(default="", max_length=128)
    do_not_disturb: bool = False


def choose_endpoint(
    endpoints: list[dict], preferences: RoutingPreferences
) -> tuple[str, str]:
    """Deterministic policy over fresh facts; never infer presence from online alone."""
    fresh = [endpoint for endpoint in endpoints if endpoint.get("fresh")]
    if preferences.do_not_disturb or any(
        e["state"].get("do_not_disturb") for e in fresh
    ):
        return "", "do_not_disturb"
    eligible = [
        e
        for e in fresh
        if e["state"].get("locked") is False and e["state"].get("activity") == "active"
    ]
    if preferences.desktop_enabled and eligible:
        eligible.sort(
            key=lambda e: (e["node_id"] != preferences.primary_node_id, e["node_id"])
        )
        return eligible[0]["node_id"], "desktop_active"
    return "", "desktop_unavailable"


def owner_key(owner: str) -> str:
    if not owner:
        raise PermissionError("An authenticated account is required")
    return sha256(owner.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RouteSelection:
    status: Literal["ready", "pending", "unknown"]
    reason: str
    node_id: str = ""


def select_route(
    record: dict, endpoints: list[dict], preferences: RoutingPreferences
) -> RouteSelection:
    node_id, reason = choose_endpoint(endpoints, preferences)
    if reason == "do_not_disturb":
        return RouteSelection("pending", reason)
    # An uncertain attempt remains pinned to the original deduplicating endpoint.
    if record["target"].startswith("desktop:"):
        node_id = record["target"].removeprefix("desktop:")
        if len(record["attempts"]) >= 3:
            return RouteSelection("unknown", "desktop_receipt_unknown")
        if not any(e["node_id"] == node_id and e.get("fresh") for e in endpoints):
            return RouteSelection("pending", "awaiting_original_desktop")
    if not node_id and not preferences.fallback_enabled:
        return RouteSelection("pending", "no_allowed_endpoint")
    return RouteSelection("ready", reason, node_id)
