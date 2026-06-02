"""ChatAddress and SessionKey — typed identity for chats and sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard

TargetType = Literal["private", "group", "channel", "thread", "unknown"]
SessionKeyKind = Literal[
    "typed",
    "typed-derived",
    "legacy",
    "legacy-derived",
    "invalid",
]
KNOWN_TARGET_TYPES: frozenset[TargetType] = frozenset(
    ("private", "group", "channel", "thread")
)
TARGET_TYPE_UNKNOWN: TargetType = "unknown"
VALID_TARGET_TYPES: frozenset[TargetType] = frozenset(
    ("private", "group", "channel", "thread", TARGET_TYPE_UNKNOWN)
)


def is_valid_target_type(value: str) -> TypeGuard[TargetType]:
    """Return whether a raw string is a valid ChatAddress target type."""
    return value in VALID_TARGET_TYPES


def normalize_target_type(value: str) -> TargetType:
    """Convert a raw string into a TargetType, raising for invalid values."""
    if is_valid_target_type(value):
        return value
    raise ValueError(
        f"Invalid target_type {value!r}; expected one of {sorted(VALID_TARGET_TYPES)}"
    )


def classify_session_key(value: str) -> SessionKeyKind:
    """Classify a persisted session id for migration/status displays."""
    try:
        key = SessionKey.parse(value)
    except ValueError:
        return "invalid"

    if key.address.is_typed:
        return "typed-derived" if key.is_derived else "typed"
    return "legacy-derived" if key.is_derived else "legacy"


@dataclass(slots=True, frozen=True)
class ChatAddress:
    """External platform address for sending/receiving messages.

    Canonical string form: ``channel:target_type:target_id[:thread_id]``.
    """

    channel: str
    target_type: TargetType
    target_id: str
    thread_id: str = ""

    _VALID_TARGET_TYPES: frozenset[TargetType] = VALID_TARGET_TYPES

    def __post_init__(self) -> None:
        if not self.channel:
            raise ValueError("channel must not be empty")
        if not self.target_id:
            raise ValueError("target_id must not be empty")
        if self.target_type not in self._VALID_TARGET_TYPES:
            raise ValueError(
                f"Invalid target_type {self.target_type!r}; "
                f"expected one of {sorted(self._VALID_TARGET_TYPES)}"
            )

    def __str__(self) -> str:
        base = f"{self.channel}:{self.target_type}:{self.target_id}"
        if self.thread_id:
            return f"{base}:{self.thread_id}"
        return base

    @property
    def is_typed(self) -> bool:
        """True when target_type is a known standard type (not 'unknown')."""
        return self.target_type in KNOWN_TARGET_TYPES

    @property
    def chat_key(self) -> str:
        """Canonical typed chat key for session lookup."""
        return str(self)

    @property
    def legacy_key(self) -> str:
        """Legacy 2-segment key for backward compatibility."""
        return f"{self.channel}:{self.target_id}"

    @classmethod
    def parse(cls, value: str) -> ChatAddress:
        """Parse a colon-separated address string.

        Handles typed (``channel:type:id``) and legacy (``channel:id``) formats.
        """
        if not value:
            raise ValueError("Cannot parse empty string as ChatAddress")

        parts = value.split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid ChatAddress (too few segments): {value!r}")

        channel = parts[0]
        if not channel:
            raise ValueError(f"Empty channel in ChatAddress: {value!r}")

        if len(parts) == 2:
            return cls(
                channel=channel,
                target_type=TARGET_TYPE_UNKNOWN,
                target_id=parts[1],
            )

        if is_valid_target_type(parts[1]):
            return cls(
                channel=channel,
                target_type=parts[1],
                target_id=parts[2],
                thread_id=parts[3] if len(parts) > 3 else "",
            )

        return cls(
            channel=channel,
            target_type=TARGET_TYPE_UNKNOWN,
            target_id=parts[1],
        )

    @classmethod
    def from_inbound(
        cls,
        platform: str,
        chat_id: str,
        *,
        is_group: bool = False,
        chat_type: str = "",
    ) -> ChatAddress:
        """Construct a ChatAddress from inbound message metadata."""
        if chat_type and chat_type in KNOWN_TARGET_TYPES:
            target_type = normalize_target_type(chat_type)
            return cls(channel=platform, target_type=target_type, target_id=chat_id)
        if chat_type == TARGET_TYPE_UNKNOWN:
            return cls(
                channel=platform,
                target_type=TARGET_TYPE_UNKNOWN,
                target_id=chat_id,
            )
        if is_group:
            return cls(channel=platform, target_type="group", target_id=chat_id)
        return cls(channel=platform, target_type=TARGET_TYPE_UNKNOWN, target_id=chat_id)


@dataclass(slots=True, frozen=True)
class SessionKey:
    """Session identity derived from a ChatAddress with an optional suffix.

    String form: ``channel:type:id[:suffix...]``.
    """

    address: ChatAddress
    suffix: str = ""

    def __str__(self) -> str:
        base = str(self.address)
        if self.suffix:
            return f"{base}:{self.suffix}"
        return base

    @property
    def is_derived(self) -> bool:
        """True when this key has a suffix (/new, cron isolated, etc.)."""
        return bool(self.suffix)

    @classmethod
    def parse(cls, value: str) -> SessionKey:
        """Parse a colon-separated session key string."""
        if not value:
            raise ValueError("Cannot parse empty string as SessionKey")

        parts = value.split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid SessionKey (too few segments): {value!r}")

        channel = parts[0]

        if len(parts) == 2:
            return cls(
                address=ChatAddress(
                    channel=channel, target_type=TARGET_TYPE_UNKNOWN, target_id=parts[1]
                )
            )

        if is_valid_target_type(parts[1]):
            address = ChatAddress(
                channel=channel,
                target_type=parts[1],
                target_id=parts[2],
            )
            suffix = ":".join(parts[3:]) if len(parts) > 3 else ""
            return cls(address=address, suffix=suffix)

        target_id = parts[1]
        suffix = ":".join(parts[2:])
        return cls(
            address=ChatAddress(
                channel=channel, target_type=TARGET_TYPE_UNKNOWN, target_id=target_id
            ),
            suffix=suffix,
        )
