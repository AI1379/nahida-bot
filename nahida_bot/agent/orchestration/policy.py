"""Coarse orchestration policy hooks."""

from __future__ import annotations

from nahida_bot.agent.orchestration.models import SubagentSpec

# Tools a child subagent may never invoke, regardless of what the parent model
# writes into ``SubagentSpec.tool_allowlist``. These either break the
# orchestration invariant (nested-agent spawning / cross-session writes), are
# channel/transport tools that do not apply to the synthetic ``platform=agent``
# child context, or are identity-administration tools that must never be
# delegated. This is the SINGLE source of truth for the system denylist
# (issue #43): the orchestrator service no longer keeps a parallel copy.
SYSTEM_CHILD_TOOL_DENYLIST: frozenset[str] = frozenset(
    {
        "agent_spawn",
        "agent_yield",
        "agent_wait",
        "agent_stop",
        "sessions_send",
        # Channel/transport delivery is not meaningful in the synthetic child
        # context and was previously observed failing in production receipts
        # (#43): keep it denied so the parent cannot enable it.
        "message",
        "desktop_announce",
        "desktop_exec",
        "desktop_file_read",
        "desktop_screenshot_capture",
        "desktop_screen_observe",
        "desktop_screenshot_send",
        "desktop_input",
        # Identity administration can never be delegated (#39 references the
        # authz module's non-delegable rule; mirror it here so the child tool
        # surface never includes the tool).
        "identity_manage",
    }
)


class OrchestrationPolicy:
    """Default coarse policy for the local orchestration MVP."""

    def __init__(
        self,
        *,
        max_child_agents_per_run: int = 5,
        system_tool_denylist: frozenset[str] | None = None,
    ) -> None:
        self.max_child_agents_per_run = max_child_agents_per_run
        # The system denylist is overridable for tests but always wins over
        # any allowlist the parent model supplies.
        self._system_tool_denylist = (
            system_tool_denylist
            if system_tool_denylist is not None
            else SYSTEM_CHILD_TOOL_DENYLIST
        )

    @property
    def system_tool_denylist(self) -> frozenset[str]:
        """Tools a child may never invoke, regardless of the parent allowlist."""
        return self._system_tool_denylist

    async def can_spawn(
        self,
        requester_session_id: str,
        spec: SubagentSpec,
        *,
        active_child_count: int,
        depth: int,
    ) -> None:
        if depth > 0:
            raise PermissionError("Subagents cannot spawn nested subagents.")
        if active_child_count >= self.max_child_agents_per_run:
            raise PermissionError(
                "Maximum active subagent count reached for this run/session."
            )
        if not requester_session_id:
            raise PermissionError("No requester session is available.")
        if not spec.task.strip():
            raise ValueError("Subagent task must not be empty.")

    async def can_read_session(
        self, requester_session_id: str, target_session_id: str
    ) -> None:
        if (
            target_session_id != requester_session_id
            and requester_session_id not in target_session_id
        ):
            raise PermissionError("Session is outside the requester scope.")

    def compute_child_tool_filter(
        self, spec: SubagentSpec
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Compute the effective ``(allowlist, denylist)`` for a child run.

        Single source of truth for child tool filtering (issue #43). The
        system denylist is always unioned into the per-spec denylist, and any
        tool that appears in the resulting denylist is stripped from the
        allowlist — so a parent cannot widen the child's capabilities by
        listing a denied tool in ``tool_allowlist``. Returns empty sets when
        the corresponding spec field is empty (an empty allowlist means "no
        allowlist restriction", handled downstream by the runner).
        """
        denylist = self._system_tool_denylist | frozenset(spec.tool_denylist)
        requested_allow = frozenset(spec.tool_allowlist)
        allowlist = requested_allow - denylist
        return allowlist, denylist
