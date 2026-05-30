"""OneBot protocol abstraction: NormalizedEvent, OneBotResponse, and protocol interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OneBotSelf:
    """Bot's own identity information."""

    platform: str = "qq"
    user_id: str = ""


@dataclass(slots=True)
class NormalizedEvent:
    """Version-independent intermediate event representation."""

    type: (
        str  # "message.private" | "message.group" | "notice.*" | "meta.*" | "request.*"
    )
    sub_type: str = ""  # "friend" | "group" | "normal" | "anonymous" | ...
    message_id: str = ""
    user_id: str = ""
    group_id: str = ""
    self: OneBotSelf = field(default_factory=OneBotSelf)
    message: list[dict[str, Any]] = field(default_factory=list)
    alt_message: str = ""
    time: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OneBotResponse:
    """Version-independent action response."""

    status: str  # "ok" | "failed"
    retcode: int = 0
    data: Any = None
    echo: str = ""
    message: str = ""


class OneBotProtocol(ABC):
    """Normalize v11/v12 differences behind a single interface."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Return "v11" or "v12"."""
        ...

    @abstractmethod
    def detect_event_type(self, raw: dict[str, Any]) -> str | None:
        """Return normalized event type string or None if not an event frame."""
        ...

    @abstractmethod
    def normalize_event(self, raw: dict[str, Any]) -> NormalizedEvent:
        """Convert a v11 or v12 event dict into a stable intermediate format."""
        ...

    @abstractmethod
    def encode_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Build the protocol-specific action payload."""
        ...

    @abstractmethod
    def decode_response(self, raw: dict[str, Any]) -> OneBotResponse:
        """Parse an action response into a stable result object."""
        ...

    @abstractmethod
    def is_api_response(self, raw: dict[str, Any]) -> bool:
        """Check whether a raw dict is an API response (vs an event)."""
        ...

    @abstractmethod
    def get_echo(self, raw: dict[str, Any]) -> str | None:
        """Extract echo field from a raw message for RPC routing."""
        ...

    @staticmethod
    def detect_version(raw: dict[str, Any]) -> str | None:
        """Detect OneBot protocol version from a raw event or response frame.

        Returns "v11", "v12", or None if undetectable.
        """
        if "post_type" in raw and raw["post_type"] in (
            "message",
            "notice",
            "request",
            "meta_event",
        ):
            return "v11"
        if "type" in raw:
            t = str(raw["type"])
            if t.startswith(("message.", "notice.", "meta.", "request.")):
                return "v12"
        if "retcode" in raw and "data" in raw:
            # Could be either version's response; check for v12 "self" field
            if "self" in raw:
                return "v12"
            return "v11"
        return None
