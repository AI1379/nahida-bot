"""Language-neutral Desktop surface contracts exposed to plugins."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DesktopSurfaceTarget = Literal[
    "desktop.home",
    "desktop.sidebar",
    "pet.overlay",
    "pet.drawer",
]
DesktopSurfaceKind = Literal[
    "text",
    "badge",
    "countdown",
    "progress",
    "list",
    "card",
]
DesktopSurfaceTone = Literal["neutral", "info", "success", "warning", "danger"]


class DesktopSurfaceItem(BaseModel):
    """One host-rendered row in a list or card surface."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=120)
    completed: bool = False


class DesktopSurfaceView(BaseModel):
    """Sanitized view model returned by a plugin surface provider."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=400)
    status: str = Field(default="", max_length=80)
    detail: str = Field(default="", max_length=120)
    expires_at: str = Field(default="", max_length=64)
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    items: list[DesktopSurfaceItem] = Field(default_factory=list, max_length=20)
    tone: DesktopSurfaceTone = "neutral"


class DesktopSurfaceContext(BaseModel):
    """Non-secret information about the Desktop requesting a snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    display_name: str = ""
    node_type: str = "desktop"
    metadata: dict[str, object] = Field(default_factory=dict)


class DesktopSurfaceSnapshotItem(BaseModel):
    """Resolved, owner-stamped contribution sent from Gateway to Desktop."""

    model_config = ConfigDict(extra="forbid")

    owner_plugin_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
    )
    target: DesktopSurfaceTarget
    kind: DesktopSurfaceKind
    priority: int = Field(default=0, ge=-100, le=100)
    view: DesktopSurfaceView


__all__ = [
    "DesktopSurfaceContext",
    "DesktopSurfaceItem",
    "DesktopSurfaceKind",
    "DesktopSurfaceSnapshotItem",
    "DesktopSurfaceTarget",
    "DesktopSurfaceTone",
    "DesktopSurfaceView",
]
