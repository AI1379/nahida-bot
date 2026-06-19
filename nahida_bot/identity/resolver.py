"""Identity resolution: InboundMessage -> IdentityResolution.

Runs on every inbound turn (when enabled) to populate ``SessionContext`` with
the sender's account key and linked person. Also records a participant
observation for audit/display. Resolution never raises — on any failure it
degrades to ``None`` so message handling is unaffected.
"""

from __future__ import annotations

from typing import cast

import structlog

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.identity.models import (
    AccountKey,
    IdentityResolution,
    LinkSource,
    ParticipantObservation,
)
from nahida_bot.identity.store import IdentityStore
from nahida_bot.plugins.base import InboundMessage

_logger = structlog.get_logger(__name__)


def account_key_from_inbound(
    inbound: InboundMessage, address: ChatAddress | None
) -> AccountKey | None:
    """Derive the sender's :class:`AccountKey`, or ``None`` if unavailable.

    Channel comes from the (typed) chat address. Caveat:
    ``ChatAddress.from_inbound`` currently sets ``channel = platform`` (the SDK
    has no instance-id concept yet), so today the channel segment *is* the
    platform name — safe for single-instance deployments, but two bots on the
    same platform would collide and that is not yet supported. The platform
    account id comes from ``SenderContext.platform_user_id`` with a legacy
    fallback to ``InboundMessage.user_id``.
    """
    # Channel currently = platform name (the SDK's from_inbound sets
    # channel=platform; no instance-id concept yet). Safe for single-instance;
    # multi-instance same-platform would collide and is unsupported. No typed
    # address means no account key.
    channel = address.channel if address is not None else ""

    sender = inbound.sender_context
    platform_user_id = ""
    if sender is not None:
        platform_user_id = sender.platform_user_id
    if not platform_user_id:
        platform_user_id = inbound.user_id

    if not channel or not platform_user_id:
        return None
    return AccountKey.from_parts(channel=channel, platform_user_id=platform_user_id)


class IdentityResolver:
    """Resolve an inbound turn to an :class:`IdentityResolution`.

    A disabled resolver is a no-op: :meth:`resolve` returns ``None`` and writes
    nothing, so the whole subsystem stays off unless ``identity.enabled`` is set.
    """

    def __init__(self, store: IdentityStore, *, enabled: bool) -> None:
        self._store = store
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def resolve(
        self,
        inbound: InboundMessage,
        address: ChatAddress | None,
        session_id: str,
    ) -> IdentityResolution | None:
        """Resolve identity for one inbound message.

        Returns ``None`` when disabled or when no account id is derivable; in
        both cases the caller leaves ``SessionContext`` identity fields empty.
        """
        if not self._enabled:
            return None

        account_key = account_key_from_inbound(inbound, address)
        if account_key is None:
            return None

        account_key_str = str(account_key)
        chat_address = address.chat_key if address is not None else ""

        await self._record_observation(inbound, account_key_str, chat_address)

        try:
            person_id, source = await self._store.resolve_account(account_key_str)
        except Exception as exc:
            _logger.warning(
                "identity.resolve_failed",
                account_key=account_key_str,
                error=str(exc),
            )
            person_id, source = None, "none"

        if person_id:
            return IdentityResolution(
                chat_address=chat_address,
                session_id=session_id,
                sender_account_key=account_key_str,
                person_id=person_id,
                confidence="linked",
                # The store only ever persists LinkSource verification values.
                source=cast(LinkSource, source),
            )
        _logger.debug(
            "identity.account_unlinked",
            account_key=account_key_str,
            chat_address=chat_address,
        )
        return IdentityResolution(
            chat_address=chat_address,
            session_id=session_id,
            sender_account_key=account_key_str,
            person_id=None,
            confidence="unlinked",
            source="none",
        )

    async def _record_observation(
        self,
        inbound: InboundMessage,
        account_key: str,
        chat_address: str,
    ) -> None:
        sender = inbound.sender_context
        display_name = sender.display_name if sender is not None else ""
        role_tags = sender.role_tags if sender is not None else ()
        try:
            await self._store.record_observation(
                ParticipantObservation(
                    chat_address=chat_address,
                    account_key=account_key,
                    display_name=display_name,
                    role_tags=tuple(role_tags),
                    last_message_id=inbound.message_id,
                )
            )
        except Exception as exc:
            # Observation is best-effort; never block message handling on it.
            _logger.debug(
                "identity.observation_failed",
                account_key=account_key,
                error=str(exc),
            )
