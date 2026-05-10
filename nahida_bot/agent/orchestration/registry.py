"""In-memory registry for active agent runs."""

from __future__ import annotations

from nahida_bot.agent.orchestration.models import AgentRun, AgentRunStatus


class AgentRegistry:
    """Tracks active and recent runs in the current process."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRun] = {}
        self._task_to_run: dict[str, str] = {}

    def register(self, run: AgentRun) -> None:
        self._runs[run.run_id] = run
        if run.task_id:
            self._task_to_run[run.task_id] = run.run_id

    def unregister(self, run_id: str) -> None:
        run = self._runs.pop(run_id, None)
        if run is not None and run.task_id:
            self._task_to_run.pop(run.task_id, None)

    def get_run(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def get_by_task(self, task_id: str) -> AgentRun | None:
        run_id = self._task_to_run.get(task_id)
        return self._runs.get(run_id) if run_id else None

    def list_for_session(self, requester_session_id: str) -> list[AgentRun]:
        return [
            run
            for run in self._runs.values()
            if run.requester_session_id == requester_session_id
        ]

    def set_status(
        self,
        run_id: str,
        status: AgentRunStatus,
        *,
        summary: str = "",
        error: str = "",
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        run.status = status
        if summary:
            run.summary = summary
        if error:
            run.error = error

    def active_child_count(self, requester_session_id: str) -> int:
        return sum(
            1
            for run in self._runs.values()
            if run.requester_session_id == requester_session_id
            and run.status in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}
        )
