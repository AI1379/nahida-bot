"""Person/account identity data models.

These types are the shared contract between the identity store, resolver, and
future identity-aware memory code (Phase 2/3). They are deliberately free of
SDK inbound-message dependencies — converting an :class:`InboundMessage` to an
:class:`AccountKey` happens in :mod:`nahida_bot.identity.resolver`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Confidence = Literal["linked", "unlinked", "unknown"]
LinkSource = Literal["manual_link", "self_verified", "config_seed", "none"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AccountKey:
    """A platform account identity, scoped to a configured channel instance.

    Canonical string form is ``{channel}:user:{platform_user_id}``. ``channel``
    is the configured channel instance id (not a vague platform name) so that
    multi-account deployments keep namespaces separate. ``platform_user_id`` is
    the stable platform-side account id (never a display name).
    """

    channel: str
    platform_user_id: str

    def __str__(self) -> str:
        return f"{self.channel}:user:{self.platform_user_id}"

    @classmethod
    def from_parts(cls, channel: str, platform_user_id: str) -> AccountKey:
        """Construct from explicit parts. Both must be non-empty."""
        return cls(channel=channel, platform_user_id=platform_user_id)

    @classmethod
    def parse(cls, value: str) -> AccountKey:
        """Parse a canonical ``{channel}:user:{platform_user_id}`` string.

        Raises ``ValueError`` on malformed input.
        """
        prefix, sep, rest = value.partition(":user:")
        if not sep or not prefix or not rest:
            raise ValueError(f"invalid account key: {value!r}")
        return cls(channel=prefix, platform_user_id=rest)


@dataclass(frozen=True, slots=True)
class Person:
    """A real chat counterpart the bot knows locally, spanning accounts."""

    person_id: str
    display_name: str = ""
    status: str = "active"
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class AccountLink:
    """An active binding of one account to a person, with provenance."""

    account_key: str
    person_id: str
    channel: str
    account_type: str = "user"
    platform_account_id: str = ""
    label: str = ""
    status: str = "active"
    verification: LinkSource = "manual_link"
    linked_by: str = ""
    linked_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True, slots=True)
class ParticipantObservation:
    """How an account appeared in a chat — for audit/display, not auto-linking."""

    chat_address: str
    account_key: str
    display_name: str = ""
    role_tags: tuple[str, ...] = ()
    first_seen_at: datetime = field(default_factory=_utc_now)
    last_seen_at: datetime = field(default_factory=_utc_now)
    last_message_id: str = ""


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    """The identity resolved for one inbound turn.

    - ``sender_account_key``: always set when an account id was derivable.
    - ``person_id``: the linked person, or ``None`` if the account is unlinked.
    - ``confidence``: ``linked`` (person known), ``unlinked`` (account known,
      no person), ``unknown`` (no account derivable).
    - ``source``: provenance of the binding (``none`` when unlinked/unknown).
    """

    chat_address: str
    session_id: str
    sender_account_key: str
    person_id: str | None
    confidence: Confidence
    source: LinkSource
