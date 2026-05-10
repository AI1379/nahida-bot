"""Persistent background task store contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

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
    ) -> None:
        raise NotImplementedError
