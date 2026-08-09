"""Restricted semantic service for agent-requested Desktop announcements."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.gateway.services.node_invoker import NodeInvoker
from nahida_bot.gateway.services.node_registry import NodeRegistry

DESKTOP_ANNOUNCE_CAPABILITY = "desktop.notification.announce"
MAX_DESKTOP_ANNOUNCEMENT_CHARS = 300


@dataclass(slots=True, frozen=True)
class DesktopAnnouncementResult:
    ok: bool
    node_id: str = ""
    error_code: str = ""
    error_message: str = ""


class DesktopAnnouncementService:
    """Send a short announcement to the caller's uniquely bound Desktop."""

    def __init__(self, registry: NodeRegistry, invoker: NodeInvoker) -> None:
        self._registry = registry
        self._invoker = invoker

    async def announce(
        self,
        *,
        message: str,
        conversation_id: str,
        actor_account_key: str,
        caller: str,
    ) -> DesktopAnnouncementResult:
        clean_message = message.strip()
        if not clean_message:
            return DesktopAnnouncementResult(
                ok=False,
                error_code="invalid_arguments",
                error_message="announcement message must not be empty",
            )
        if len(clean_message) > MAX_DESKTOP_ANNOUNCEMENT_CHARS:
            return DesktopAnnouncementResult(
                ok=False,
                error_code="invalid_arguments",
                error_message=(
                    "announcement message exceeds "
                    f"{MAX_DESKTOP_ANNOUNCEMENT_CHARS} characters"
                ),
            )

        candidates = self._registry.find_bound_capability_owners(
            capability=DESKTOP_ANNOUNCE_CAPABILITY,
            conversation_id=conversation_id,
            actor_account_key=actor_account_key,
        )
        if not candidates:
            return DesktopAnnouncementResult(
                ok=False,
                error_code="desktop_unavailable",
                error_message="no online Desktop is bound to this conversation or actor",
            )
        if len(candidates) > 1:
            return DesktopAnnouncementResult(
                ok=False,
                error_code="ambiguous_desktop",
                error_message="multiple online Desktops match this actor",
            )

        target = candidates[0]
        invoked = await self._invoker.invoke(
            capability=DESKTOP_ANNOUNCE_CAPABILITY,
            arguments={"message": clean_message},
            caller=caller,
            node_id=target.node_id,
        )
        if not invoked.ok:
            return DesktopAnnouncementResult(
                ok=False,
                node_id=target.node_id,
                error_code=(
                    invoked.error.code if invoked.error is not None else "failed"
                ),
                error_message=(
                    invoked.error.message
                    if invoked.error is not None
                    else "Desktop rejected the announcement"
                ),
            )
        return DesktopAnnouncementResult(ok=True, node_id=target.node_id)


__all__ = [
    "DESKTOP_ANNOUNCE_CAPABILITY",
    "MAX_DESKTOP_ANNOUNCEMENT_CHARS",
    "DesktopAnnouncementResult",
    "DesktopAnnouncementService",
]
