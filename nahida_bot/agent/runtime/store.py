"""Storage interface for the canonical agent-run ledger (Phase 1).

:class:`AgentRunStore` is the abstract persistence boundary. The agent loop
talks only to :class:`~nahida_bot.agent.runtime.recorder.RunRecorder`, which
sequencing + sanitizes and then calls this interface. Phase 1 ships a SQLite
implementation and a :class:`NullAgentRunStore` used when the ledger is
disabled (so the loop can call unconditionally).

Invariant: events may only be appended while a run is ``running``. Appending
after the run is finalized raises :class:`AgentRunClosedError` (the recorder
treats any write error as best-effort — log and continue).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from nahida_bot.agent.runtime.models import (
    AgentRunContext,
    ExecutionReceipt,
    TerminalState,
)


class AgentRunClosedError(RuntimeError):
    """Raised when appending an event/receipt to an already-finalized run."""


class AgentRunStore(ABC):
    """Abstract persistence boundary for canonical run records."""

    @abstractmethod
    async def create_run(
        self,
        context: AgentRunContext,
        *,
        model: str = "",
        api_family: str = "",
        started_at: str,
    ) -> None:
        """Insert a new run row in the ``running`` state."""
        raise NotImplementedError

    @abstractmethod
    async def append_event(
        self,
        run_id: str,
        *,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        """Append one canonical event. Rejects if the run is not ``running``."""
        raise NotImplementedError

    @abstractmethod
    async def record_receipt(self, receipt: ExecutionReceipt) -> None:
        """Upsert an execution receipt keyed by ``(run_id, call_id)``."""
        raise NotImplementedError

    @abstractmethod
    async def finalize_run(
        self,
        run_id: str,
        *,
        terminal_state: TerminalState,
        ended_at: str,
        failure_code: str = "",
        failure_detail: str = "",
    ) -> None:
        """Mark a run terminal. Idempotent: a no-op if already finalized."""
        raise NotImplementedError

    @abstractmethod
    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        """Return the run's events ordered by sequence."""
        raise NotImplementedError

    @abstractmethod
    async def list_receipts(self, run_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def save_transcript(
        self, run_id: str, messages: list[dict[str, Any]]
    ) -> None:
        """Persist a run's ordered raw transcript for cross-turn replay.

        ``messages`` is a JSON-serializable list of ContextMessage dicts
        (the loop's ``active_turn_messages`` snapshot). Best-effort and
        idempotent (overwrite). Written AFTER the run is finalized, so this
        intentionally does NOT go through the running-state guard that
        :meth:`append_event` enforces.
        """
        raise NotImplementedError

    @abstractmethod
    async def list_recent_transcripts(
        self, session_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return ``[{run_id, started_at, terminal_state, transcript_json}]``
        for the session's most recent runs that HAVE a transcript, ordered
        oldest-first. Runs with NULL ``transcript_json`` are excluded.
        """
        raise NotImplementedError


class NullAgentRunStore(AgentRunStore):
    """No-op store used when the ledger is disabled.

    Every method is a no-op so the agent loop can call the recorder
    unconditionally without branching on a flag.
    """

    async def create_run(
        self,
        context: AgentRunContext,
        *,
        model: str = "",
        api_family: str = "",
        started_at: str,
    ) -> None:
        return None

    async def append_event(
        self,
        run_id: str,
        *,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        return None

    async def record_receipt(self, receipt: ExecutionReceipt) -> None:
        return None

    async def finalize_run(
        self,
        run_id: str,
        *,
        terminal_state: TerminalState,
        ended_at: str,
        failure_code: str = "",
        failure_detail: str = "",
    ) -> None:
        return None

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return None

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        return []

    async def list_receipts(self, run_id: str) -> list[dict[str, Any]]:
        return []

    async def save_transcript(
        self, run_id: str, messages: list[dict[str, Any]]
    ) -> None:
        return None

    async def list_recent_transcripts(
        self, session_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        return []
