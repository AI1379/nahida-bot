"""Persistent background task store contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from nahida_bot.agent.orchestration.models import AgentRunStatus, BackgroundTask


class BackgroundTaskStore(ABC):
    """Persistence contract for orchestration background tasks."""

    @abstractmethod
    async def create(self, task: BackgroundTask) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, task_id: str) -> BackgroundTask | None:
        raise NotImplementedError

    @abstractmethod
    async def list_for_session(
        self, requester_session_id: str, *, limit: int = 20
    ) -> list[BackgroundTask]:
        raise NotImplementedError

    @abstractmethod
    async def update_status(
        self,
        task_id: str,
        status: AgentRunStatus,
        *,
        summary: str = "",
        error: str = "",
        terminal: bool = False,
        terminal_state: str = "",
        terminal_reason: str = "",
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def claim_delivery(
        self,
        task_id: str,
        *,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> bool:
        """Atomically claim a pending delivery, reclaiming stale claims."""
        raise NotImplementedError

    @abstractmethod
    async def release_delivery_claim(
        self, task_id: str, *, claimed_at: datetime
    ) -> bool:
        """Release the caller's claim after a delivery attempt fails."""
        raise NotImplementedError

    @abstractmethod
    async def mark_delivered(
        self,
        task_id: str,
        *,
        claimed_at: datetime,
        delivered_at: datetime,
    ) -> bool:
        """Confirm delivery held by the matching claim owner.

        Returns True only when the claim still belongs to this caller and the
        task had not already been delivered.
        """
        raise NotImplementedError
