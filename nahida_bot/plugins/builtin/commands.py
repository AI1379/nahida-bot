"""Builtin commands plugin — commands, workspace tools, exec, web_fetch, plan."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from nahida_bot_sdk.commands import CommandArgument, CompletionChoice, CompletionQuery

from nahida_bot.plugins.base import (
    BotAPI,
    InboundMessage,
    Plugin,
    PluginManifest,
)
from nahida_bot.plugins.builtin.tools.agent import AgentTools
from nahida_bot.plugins.builtin.tools.context import (
    address_from_inbound as _address_from_inbound,
    typed_address_from_session_context as _typed_address_from_session_context,
)
from nahida_bot.plugins.builtin.tools.cron import CronTools
from nahida_bot.plugins.builtin.tools.desktop import DesktopTools
from nahida_bot.plugins.builtin.tools.history import HistoryTools
from nahida_bot.plugins.builtin.tools.memory import MemoryTools
from nahida_bot.plugins.builtin.tools.message import MessageTools
from nahida_bot.plugins.builtin.tools.plan import PlanTools
from nahida_bot.plugins.builtin.tools.web_fetch import WebFetchTools
from nahida_bot.plugins.builtin.tools.workspace import WorkspaceTools
from nahida_bot.plugins.tooling import register_tool_definitions

from nahida_bot.core.chat_address import (
    SessionKey,
    classify_session_key,
)
from nahida_bot.core.context import current_session
from nahida_bot.core.events import AgentStopPayload, AgentStopRequested
from nahida_bot.core.runtime_settings import (
    REASONING_EFFORTS,
    runtime_settings_from_meta,
)

_logger = structlog.get_logger(__name__)

_MAX_EXEC_OUTPUT = 50_000
_MAX_EXEC_TIMEOUT = 120


class BuiltinCommandsPlugin(Plugin):
    """Registers core commands and built-in tools."""

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._agent_tools = AgentTools(api)
        self._cron_tools = CronTools(api)
        self._desktop_tools = DesktopTools(api)
        self._history_tools = HistoryTools(api)
        self._memory_tools = MemoryTools(api)
        self._message_tools = MessageTools(api, manifest.config)
        self._plan_tools = PlanTools(api)
        self._workspace_tools = WorkspaceTools(api)
        self._web_fetch_tools = WebFetchTools()

    async def on_load(self) -> None:
        self._register_commands()
        self._register_workspace_tools()
        self._register_attachment_tools()
        self._register_memory_tools()
        self._register_history_tools()
        self._register_exec_tool()
        self._register_web_fetch_tool()
        self._register_plan_tool()
        self._register_cron_tools()
        self._register_agent_tools()
        self._register_message_tool()
        self._register_desktop_announce_tool()
        self._register_desktop_control_tools()
        self._register_skill_tool()
        self._register_identity_tool()

    # ── Command Registration ────────────────────────────────

    def _register_commands(self) -> None:
        self.api.register_command(
            "reset",
            self._cmd_reset,
            description="Clear current session history",
            aliases=["r"],
        )
        self.api.register_command(
            "new", self._cmd_new, description="Start a new conversation session"
        )
        self.api.register_command(
            "status",
            self._cmd_status,
            description="Show session and model info",
            aliases=["info"],
        )
        self.api.register_command(
            "quota",
            self._cmd_quota,
            description="Show provider-reported balances and subscription quotas",
        )
        self.api.register_command(
            "model",
            self._cmd_model,
            description="List or switch model (/model [name])",
            arguments=[
                CommandArgument(
                    name="name",
                    description="Model to switch to (omit to list available models)",
                    completer=self._complete_model_names,
                )
            ],
        )
        self.api.register_command(
            "reasoning",
            self._cmd_reasoning,
            description=(
                "Show or change reasoning settings "
                "(/reasoning on|off|effort <level>|reset)"
            ),
            aliases=["think"],
            arguments=[
                CommandArgument(
                    name="action",
                    description="on / off / effort / reset",
                    choices=("on", "off", "effort", "reset"),
                ),
                CommandArgument(
                    name="level",
                    description="Effort level for 'effort' (e.g. low / medium / high)",
                ),
            ],
        )
        self.api.register_command(
            "help", self._cmd_help, description="List available commands"
        )
        self.api.register_command(
            "memory",
            self._cmd_memory,
            description="Search or store durable memory",
        )
        self.api.register_command(
            "agents",
            self._cmd_agents,
            description="List subagent tasks for this session",
            aliases=["agent"],
        )
        self.api.register_command(
            "agent_stop",
            self._cmd_agent_stop,
            description="Stop a running subagent task (/agent_stop <task_id>)",
            arguments=[
                CommandArgument(
                    name="task_id", description="Task id to stop", required=True
                )
            ],
        )
        self.api.register_command(
            "agent_wait",
            self._cmd_agent_wait,
            description="Wait for a subagent task to finish (/agent_wait <task_id> [timeout])",
        )
        self.api.register_command(
            "cron",
            self._cmd_cron,
            description="Manage scheduled tasks (/cron list|cancel|delete <id>)",
            arguments=[
                CommandArgument(
                    name="action",
                    description="list / cancel / delete",
                    choices=("list", "cancel", "delete"),
                ),
                CommandArgument(name="id", description="Task id for cancel/delete"),
            ],
        )
        self.api.register_command(
            "stop",
            self._cmd_stop,
            description="Stop the currently running agent",
            aliases=["s"],
        )
        self.api.register_command(
            "identity",
            self._cmd_identity,
            description="Show your resolved identity (/identity whoami)",
            arguments=[
                CommandArgument(
                    name="action", description="whoami", choices=("whoami",)
                ),
                CommandArgument(
                    name="args", description="Action arguments (create/link/remove)"
                ),
            ],
        )

    def _register_workspace_tools(self) -> None:
        register_tool_definitions(self.api, self._workspace_tools.definitions())

    def _register_attachment_tools(self) -> None:
        register_tool_definitions(
            self.api, self._message_tools.attachment_definitions()
        )

    def _register_memory_tools(self) -> None:
        register_tool_definitions(self.api, self._memory_tools.definitions())

    # ── Cross-session history & chat lookup ────────────────

    def _register_history_tools(self) -> None:
        register_tool_definitions(self.api, self._history_tools.definitions())

    # ── exec Tool ──────────────────────────────────────────

    def _register_exec_tool(self) -> None:
        self.api.register_tool(
            "exec",
            "Execute a shell command and return its stdout, stderr, and exit code. "
            "The command runs with its working directory set to the current workspace "
            "root unless working_dir is given (also relative to the workspace root). "
            "Do not pass absolute paths from exec into workspace_read/workspace_write: "
            "those tools only accept workspace-relative paths.",
            {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30, max 120).",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": (
                            "Working directory relative to the workspace root. "
                            "Defaults to the workspace root itself."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            self._tool_exec,
            requires_admin=True,
        )

    async def _tool_exec(
        self,
        command: str,
        timeout: int = 30,
        working_dir: str = "",
    ) -> str:
        _logger.debug("tool.exec", command=command, timeout=timeout, cwd=working_dir)

        actual_timeout = min(max(timeout, 1), _MAX_EXEC_TIMEOUT)

        # Issue #40: resolve cwd against the current session's workspace so
        # exec and workspace_read/write share the same root. An empty
        # working_dir previously inherited the Bot process directory (often
        # the source repo), which then produced absolute paths the workspace
        # sandbox rejects. Fall back to the legacy behaviour only when there
        # is no resolvable workspace root.
        cwd = self._resolve_exec_cwd(working_dir)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=actual_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Command timed out after {actual_timeout}s.\nCommand: {command}"

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            output = f"Exit code: {proc.returncode}\n"
            if stdout:
                output += f"--- stdout ---\n{stdout}"
            if stderr:
                output += f"--- stderr ---\n{stderr}"

            if len(output) > _MAX_EXEC_OUTPUT:
                output = output[:_MAX_EXEC_OUTPUT] + "\n... (output truncated)"

            return output

        except Exception as e:
            _logger.exception("tool.exec.error", command=command)
            return f"Failed to execute command: {e}"

    def _resolve_exec_cwd(self, working_dir: str) -> str | None:
        """Resolve the exec working directory against the current workspace.

        Issue #40: ``exec`` and ``workspace_read``/``workspace_write`` must
        share the same root. ``working_dir`` (when given) is interpreted as
        relative to the workspace root; an empty ``working_dir`` defaults to
        the workspace root itself rather than the Bot process directory.
        Returns ``None`` only when no workspace can be resolved, preserving
        the legacy "inherit process cwd" behaviour for workspace-less setups.
        """
        from pathlib import Path

        get_workspace_root = getattr(self.api, "get_workspace_root", None)
        if not callable(get_workspace_root):
            return working_dir or None
        try:
            root_str = get_workspace_root()
        except Exception:
            _logger.debug("tool.exec.workspace_root_unavailable")
            return working_dir or None
        if not isinstance(root_str, str) or not root_str:
            return working_dir or None
        root = Path(root_str)
        if not working_dir:
            return str(root)
        # working_dir is workspace-relative; keep it inside the workspace so
        # exec cannot accidentally escape to an unrelated directory.
        candidate = (root / working_dir).resolve(strict=False)
        try:
            candidate.relative_to(root.resolve(strict=False))
        except ValueError:
            _logger.warning(
                "tool.exec.working_dir_outside_workspace",
                working_dir=working_dir,
                workspace_root=str(root),
            )
            return str(root)
        return str(candidate)

    # ── web_fetch Tool ─────────────────────────────────────

    def _register_web_fetch_tool(self) -> None:
        register_tool_definitions(self.api, self._web_fetch_tools.definitions())

    # ── plan Tool ──────────────────────────────────────────

    def _register_plan_tool(self) -> None:
        register_tool_definitions(self.api, self._plan_tools.definitions())

    # ── Cron Tools ─────────────────────────────────────────

    def _register_cron_tools(self) -> None:
        register_tool_definitions(self.api, self._cron_tools.definitions())

    # ── Agent Orchestration Tools ────────────────────────

    def _register_agent_tools(self) -> None:
        register_tool_definitions(self.api, self._agent_tools.definitions())

    def _get_orchestrator(self) -> Any:
        return self._agent_tools.orchestrator

    @staticmethod
    def _format_background_task(task: Any) -> str:
        return AgentTools.format_background_task(task)

    # ── Cross-Session Message Tool ───────────────────────

    def _register_desktop_announce_tool(self) -> None:
        self.api.register_tool(
            "desktop_announce",
            (
                "Speak a short announcement on the current owner's Desktop. "
                "This is only available during scheduled CRON runs. Use it for "
                "time-sensitive or important alerts, not routine checks. Keep the "
                "message concise and spoken-language friendly. The normal final "
                "response must still be returned for the configured chat channel."
            ),
            {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 300,
                        "description": "Short spoken announcement, at most 300 characters.",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            self._tool_desktop_announce,
        )

    async def _tool_desktop_announce(self, message: str) -> str:
        ctx = current_session.get()
        if ctx is None or ctx.origin != "cron_trigger":
            return "Error: desktop_announce is only available during CRON runs."
        service = getattr(self.api, "desktop_announcement_service", None)
        if service is None:
            return "Error: Desktop announcement service is unavailable."
        result = await service.announce(
            message=message,
            conversation_id=ctx.effective_conversation_id,
            actor_account_key=ctx.actor_account_key,
            caller=f"agent:cron:{ctx.session_id}",
        )
        if not result.ok:
            return f"Error: Desktop announcement failed ({result.error_code}): {result.error_message}"
        return f"Desktop announcement queued on {result.node_id}."

    def _register_desktop_control_tools(self) -> None:
        register_tool_definitions(self.api, self._desktop_tools.definitions())

    def _register_message_tool(self) -> None:
        register_tool_definitions(self.api, self._message_tools.message_definitions())

    # ── Command Handlers ──────────────────────────────────

    async def _cmd_cron(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        raw = args.strip()
        if not raw or raw == "list":
            return await self._cron_list(inbound)
        action, _, rest = raw.partition(" ")
        action = action.lower()
        if action == "cancel":
            return await self._cron_cancel(rest.strip(), inbound)
        if action == "delete":
            return await self._cron_delete(rest.strip(), inbound)
        return (
            "Usage:\n"
            "  /cron          — List your scheduled tasks\n"
            "  /cron list     — List your scheduled tasks\n"
            "  /cron cancel <id> — Cancel a task\n"
            "  /cron delete <id> — Permanently delete a task"
        )

    async def _cron_list(self, inbound: InboundMessage) -> str:
        return await self._cron_tools.list_for_inbound(inbound)

    async def _cron_cancel(self, job_id: str, inbound: InboundMessage) -> str:
        return await self._cron_tools.cancel_for_inbound(job_id, inbound)

    async def _cron_delete(self, job_id: str, inbound: InboundMessage) -> str:
        return await self._cron_tools.delete_for_inbound(job_id, inbound)

    async def _cmd_reset(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        _logger.debug(
            "cmd.reset",
            session_id=session_id,
            platform=inbound.platform,
            chat_id=inbound.chat_id,
        )
        deleted = await self.api.clear_session(session_id)
        _logger.debug("cmd.reset.done", session_id=session_id, deleted=deleted)
        return f"Session cleared. {deleted} message(s) removed."

    async def _cmd_new(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        _logger.debug(
            "cmd.new.attempt",
            old_session_id=session_id,
            platform=inbound.platform,
            chat_id=inbound.chat_id,
        )

        ctx = current_session.get()
        address = _typed_address_from_session_context(ctx) if ctx is not None else None
        if address is None:
            inbound_address = _address_from_inbound(inbound)
            address = inbound_address if inbound_address.is_typed else None
        if address is None:
            return "Failed to create new session: current chat type is unavailable."

        new_id = await self.api.start_new_session(address)
        if new_id is not None:
            _logger.debug("cmd.new.success", new_session_id=new_id)
            return f"New session started: {new_id}"
        _logger.warning("cmd.new.no_router")
        return "Failed to create new session — router not available."

    async def _cmd_status(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        info = await self.api.get_session_info(session_id)
        provider_id = info.get("provider_id", "(default)")
        model = info.get("model", "(default)")
        runtime = runtime_settings_from_meta(info)
        reasoning_display = self._format_optional_bool(runtime.reasoning.show)
        reasoning_effort = runtime.reasoning.effort or "default"

        lines = [
            f"Session: {session_id}",
            f"Session key: {_format_session_key_kind(classify_session_key(session_id))}",
            f"Provider: {provider_id}",
            f"Model: {model}",
        ]

        run_status = self.api.get_session_run_status(session_id)
        agent_line = self._format_agent_status(run_status)
        lines.append(f"Agent: {agent_line}")

        lines.extend(
            [
                f"Reasoning display: {reasoning_display}",
                f"Reasoning effort: {reasoning_effort}",
            ]
        )

        # Collect supplementary status from registered providers.
        try:
            chat_key = SessionKey.parse(session_id).address.chat_key
        except ValueError:
            chat_key = ""
        provider_blocks = await self.api.collect_status_providers(
            session_id=session_id,
            chat_key=chat_key,
        )
        for block in provider_blocks:
            lines.append("")
            lines.append(block)

        return "\n".join(lines)

    async def _cmd_quota(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        """Show provider-owned quota data; this is intentionally public."""
        del inbound, session_id
        parts = args.strip().split()
        force_refresh = any(part.lower() in {"refresh", "force"} for part in parts)
        provider_id = next(
            (part for part in parts if part.lower() not in {"refresh", "force", "all"}),
            "",
        )
        reports = await self.api.query_provider_quota(
            provider_id,
            force_refresh=force_refresh,
        )
        if not reports:
            return "No configured providers are available."

        lines = ["Provider quota"]
        for report in reports:
            report_id = str(report.get("provider_id") or "unknown")
            snapshot = report.get("snapshot")
            if not isinstance(snapshot, dict):
                error = str(report.get("error") or "Quota unavailable")
                lines.append(f"\n{report_id}: {error}")
                continue

            label = str(snapshot.get("provider_label") or report_id)
            plan_name = snapshot.get("plan_name")
            title = f"\n{report_id} ({label})"
            if plan_name:
                title += f" - {plan_name}"
            lines.append(title)
            windows = snapshot.get("windows")
            if isinstance(windows, list):
                for window in windows:
                    if not isinstance(window, dict):
                        continue
                    name = str(window.get("name") or "Quota")
                    remaining = window.get("percent_remaining")
                    used = window.get("used")
                    limit = window.get("limit")
                    unit = str(window.get("unit") or "")
                    if used is not None and limit is not None:
                        value = f"{used:g}/{limit:g} {unit}".strip()
                    elif limit is not None:
                        value = f"{limit:g} {unit}".strip()
                    elif remaining is not None:
                        value = f"{remaining:g}% remaining"
                    else:
                        value = "available"
                    reset_at = window.get("reset_at")
                    if reset_at:
                        value += f"; reset at {reset_at}"
                    lines.append(f"  {name}: {value}")
            error = report.get("error")
            if error:
                lines.append(f"  Note: {error} (showing last successful result)")

        return "\n".join(lines)

    async def _cmd_identity(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        parts = args.strip().split()
        action = parts[0].lower() if parts else "whoami"
        if action not in {
            "whoami",
            "people",
            "observations",
            "create",
            "link",
            "unlink",
        }:
            return self._identity_usage()
        ctx = current_session.get()
        if ctx is None:
            return "No active session context."
        if action != "whoami":
            try:
                return await self._run_identity_management(action, parts[1:])
            except Exception as exc:
                return f"Identity operation failed: {exc}"
        lines = [f"Session: {session_id}"]
        if ctx.sender_account_key:
            lines.append(f"Account: {ctx.sender_account_key}")
        else:
            lines.append(
                "Account: (unresolved — identity disabled or no platform account id)"
            )
        lines.append(
            f"Person: {ctx.person_id}" if ctx.person_id else "Person: (unlinked)"
        )
        if ctx.sender_display_name:
            lines.append(f"Display name: {ctx.sender_display_name}")
        return "\n".join(lines)

    async def _run_identity_management(self, action: str, args: list[str]) -> str:
        if action == "people":
            result = await self.api.identity_manage("list")
            people = result.get("people", [])
            if not people:
                return "No people configured."
            return "\n".join(
                f"{item.get('person_id')}: {item.get('display_name') or '-'} "
                f"[{', '.join(item.get('accounts', [])) or 'no accounts'}]"
                for item in people
                if isinstance(item, dict)
            )
        if action == "observations":
            account_key = args[0] if args else ""
            result = await self.api.identity_manage(
                "observations",
                account_key=account_key,
            )
            rows = result.get("observations", [])
            if not rows:
                return "No identity observations."
            return "\n".join(
                f"{item.get('account_key')} in {item.get('chat_address')} "
                f"({item.get('display_name') or '-'})"
                for item in rows
                if isinstance(item, dict)
            )
        if action == "create" and args:
            result = await self.api.identity_manage(
                "create",
                person_id=args[0],
                display_name=" ".join(args[1:]),
            )
            return f"Person saved: {result.get('person_id')}"
        if action == "link" and len(args) == 2:
            result = await self.api.identity_manage(
                "link",
                account_key=args[0],
                person_id=args[1],
            )
            return f"Linked {result.get('account_key')} -> {result.get('person_id')}"
        if action == "unlink" and len(args) == 1:
            result = await self.api.identity_manage("unlink", account_key=args[0])
            return (
                f"Unlinked {args[0]}"
                if result.get("unlinked")
                else f"No active link for {args[0]}"
            )
        return self._identity_usage()

    @staticmethod
    def _identity_usage() -> str:
        return (
            "Usage:\n"
            "  /identity whoami\n"
            "  /identity people\n"
            "  /identity observations [account_key]\n"
            "  /identity create <person_id> [display name]\n"
            "  /identity link <account_key> <person_id>\n"
            "  /identity unlink <account_key>"
        )

    @staticmethod
    def _format_agent_status(status: dict) -> str:
        state = status.get("state", "idle")
        if state == "idle":
            parts = ["idle"]
            pending = status.get("pending_messages", 0)
            if pending:
                parts.append(f"({pending} queued)")
            return " ".join(parts)
        if state == "running":
            elapsed = status.get("elapsed_seconds", 0)
            return f"running ({elapsed:.1f}s)"
        if state == "crashed":
            error = status.get("error", "unknown")
            return f"crashed — {error}"
        if state == "cancelled":
            return "cancelled"
        if state == "done":
            return "done (awaiting cleanup)"
        return f"unknown ({state})"

    async def _complete_model_names(
        self, query: CompletionQuery
    ) -> list[CompletionChoice]:
        """Suggest available models from the provider registry (fast, local)."""
        partial = query.partial.strip().lower()
        choices: list[CompletionChoice] = []
        for entry in self.api.list_models():
            model = entry.get("model", "")
            provider = entry.get("provider_id", "")
            if partial and not model.lower().startswith(partial):
                continue
            choices.append(
                CompletionChoice(
                    value=model,
                    display=model,
                    description=provider,
                )
            )
        return choices[:25]

    async def _cmd_model(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        if not args.strip():
            models = self.api.list_models()
            if not models:
                return "No providers configured."
            info = await self.api.get_session_info(session_id)
            current_model = info.get("model", "")
            current_provider = info.get("provider_id", "")
            _logger.debug(
                "cmd.model.list",
                session_id=session_id,
                current_provider=current_provider,
                current_model=current_model,
                model_count=len(models),
            )
            lines = ["Available models:"]
            for entry in models:
                marker = (
                    " (current)"
                    if entry["provider_id"] == current_provider
                    and entry["model"] == current_model
                    else ""
                )
                lines.append(f"  {entry['provider_id']}/{entry['model']}{marker}")
            return "\n".join(lines)

        model_name = args.strip()
        _logger.debug(
            "cmd.model.switch_attempt",
            session_id=session_id,
            requested_model=model_name,
        )
        provider_id = await self.api.set_session_model(session_id, model_name)
        if provider_id is not None:
            _logger.debug(
                "cmd.model.switch_success",
                session_id=session_id,
                requested_model=model_name,
                provider_id=provider_id,
            )
            return f"Switched to {model_name} (via {provider_id})"
        _logger.debug(
            "cmd.model.switch_not_found",
            session_id=session_id,
            requested_model=model_name,
        )
        return f"Model '{model_name}' not found in any provider."

    async def _cmd_reasoning(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        # TODO(runtime-commands): once runtime commands grow beyond a few simple
        # subcommands, replace this ad hoc string splitting with a shared command
        # parser that supports subcommands, typed arguments, validation, and help.
        raw = args.strip()
        if not raw:
            return await self._format_reasoning_settings(session_id)

        parts = raw.split()
        action = parts[0].lower()
        if action in {"on", "enable", "enabled", "show"}:
            await self.api.update_runtime_settings(
                session_id, {"reasoning": {"show": True}}
            )
            return await self._format_reasoning_settings(
                session_id,
                prefix="Reasoning display enabled for this session.",
            )

        if action in {"off", "disable", "disabled", "hide"}:
            await self.api.update_runtime_settings(
                session_id, {"reasoning": {"show": False}}
            )
            return await self._format_reasoning_settings(
                session_id,
                prefix="Reasoning display disabled for this session.",
            )

        if action in {"effort", "eff"}:
            if len(parts) < 2:
                return "Usage: /reasoning effort <low|medium|high|max|default>"
            effort = parts[1].lower()
            if effort in {"default", "reset", "none"}:
                await self.api.update_runtime_settings(
                    session_id, {"reasoning": {"effort": None}}
                )
                return await self._format_reasoning_settings(
                    session_id,
                    prefix="Reasoning effort reset to provider default.",
                )
            if effort not in REASONING_EFFORTS:
                allowed = ", ".join(sorted(REASONING_EFFORTS))
                return f"Invalid reasoning effort '{effort}'. Use: {allowed}, default."
            await self.api.update_runtime_settings(
                session_id, {"reasoning": {"effort": effort}}
            )
            return await self._format_reasoning_settings(
                session_id,
                prefix=f"Reasoning effort set to {effort} for this session.",
            )

        if action in {"reset", "default"}:
            await self.api.update_runtime_settings(session_id, {"reasoning": None})
            return await self._format_reasoning_settings(
                session_id,
                prefix="Reasoning settings reset to defaults for this session.",
            )

        return "\n".join(
            [
                "Usage:",
                "  /reasoning",
                "  /reasoning on",
                "  /reasoning off",
                "  /reasoning effort <low|medium|high|max|default>",
                "  /reasoning reset",
            ]
        )

    async def _format_reasoning_settings(
        self, session_id: str, *, prefix: str = ""
    ) -> str:
        info = await self.api.get_session_info(session_id)
        runtime = runtime_settings_from_meta(info)
        lines: list[str] = []
        if prefix:
            lines.append(prefix)
        lines.extend(
            [
                "Current reasoning settings:",
                f"  display: {self._format_optional_bool(runtime.reasoning.show)}",
                f"  effort: {runtime.reasoning.effort or 'default'}",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _format_optional_bool(value: bool | None) -> str:
        if value is True:
            return "on"
        if value is False:
            return "off"
        return "default"

    # ── Skill Tool ───────────────────────────────────────

    def _register_skill_tool(self) -> None:
        self.api.register_tool(
            "skill",
            (
                "Load the full instructions of a workspace skill by name. "
                "Use this when the user's request matches a skill's description "
                "but they did not explicitly invoke it via /<name>. "
                "Returns the skill's complete instructions for you to follow."
            ),
            {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Skill name to load (e.g. 'memory', 'milky-qq'). "
                            "Must match an available skill from the skill catalog."
                        ),
                    },
                    "args": {
                        "type": "string",
                        "description": "Optional context or arguments to pass to the skill.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            self._tool_skill,
        )

    async def _tool_skill(self, name: str, args: str = "") -> str:
        from nahida_bot.agent.context import SkillCatalog

        ctx = current_session.get()
        if ctx is None or not ctx.workspace_id:
            return "Error: No active workspace. Skills require a workspace context."
        # TODO: Move the skill management to WorkspaceManager
        workspace_root_str = self.api.get_workspace_root(ctx.workspace_id)
        if workspace_root_str is None:
            return "Error: Workspace manager is not available."
        workspace_root = Path(workspace_root_str)
        content = SkillCatalog.load_skill_content(workspace_root, name)
        if content is None:
            available = SkillCatalog.list_skill_names(workspace_root)
            names = ", ".join(sorted(available)) if available else "(none)"
            return (
                f"Error: Skill '{name}' not found in the active workspace. "
                f"Available skills: {names}"
            )
        if args:
            return f"{content}\n\n---\nUser context: {args}"
        return content

    async def _cmd_help(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        from nahida_bot.agent.context import SkillCatalog

        commands = self.api.list_commands()
        lines: list[str] = []

        if commands:
            lines.append("Available commands:")
            for cmd in sorted(commands, key=lambda c: c.name):
                aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                desc = f" — {cmd.description}" if cmd.description else ""
                lines.append(f"  /{cmd.name}{aliases}{desc}")

        # Append available skills
        ctx = current_session.get()
        if ctx and ctx.workspace_id:
            try:
                workspace_root_str = self.api.get_workspace_root(ctx.workspace_id)
                if workspace_root_str is None:
                    raise ValueError("workspace not available")
                workspace_root = Path(workspace_root_str)
                skills = SkillCatalog.scan_catalog(workspace_root)
                if skills:
                    lines.append("")
                    lines.append(
                        "Available skills (use /<name> or let the agent invoke them):"
                    )
                    for skill in sorted(skills, key=lambda s: s.name):
                        desc = (
                            f" — {skill.description[:100]}" if skill.description else ""
                        )
                        lines.append(f"  /{skill.name}{desc}")
            except Exception:
                pass  # Non-critical; help still shows commands

        if not lines:
            return "No commands or skills available."
        return "\n".join(lines)

    async def _cmd_memory(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        raw = args.strip()
        if not raw:
            lines = [
                "Usage:",
                "  /memory search <query>",
                "  /memory list",
                "  /memory remember <text>",
            ]
            return "\n".join(lines)

        action, _, rest = raw.partition(" ")
        action = action.lower()
        if action == "search":
            query = rest.strip()
            if not query:
                return "Usage: /memory search <query>"
            results = await self.api.memory_search(query, limit=10)
            return self._format_memory_refs(results)

        if action == "list":
            results = await self.api.memory_search("", limit=10)
            return self._format_memory_refs(results)

        if action in {"remember", "store"}:
            content = rest.strip()
            if not content:
                return "Usage: /memory remember <text>"
            await self.api.memory_store(
                "",
                content,
                metadata={
                    "source": "command",
                    "session_id": session_id,
                    "platform": inbound.platform,
                    "chat_id": inbound.chat_id,
                    "user_id": inbound.user_id,
                },
            )
            return "Memory stored."

        return "Unknown memory action. Use search, list, or remember."

    @staticmethod
    def _format_memory_refs(results: list[Any]) -> str:
        return MemoryTools.format_refs(results)

    async def _cmd_agents(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Subagent service is not available."
        tasks = await orchestrator.list_tasks(session_id)
        if not tasks:
            return "No subagent tasks for this session."
        return "\n".join(self._format_background_task(t) for t in tasks)

    async def _cmd_agent_stop(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        task_id = args.strip()
        if not task_id:
            return "Usage: /agent_stop <task_id>"
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Subagent service is not available."
        task = await orchestrator.stop_task(session_id, task_id)
        if task is None:
            return f"Task {task_id} not found or not owned by this session."
        return f"Stopped: {task.task_id} ({task.status.value})"

    async def _cmd_agent_wait(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        parts = args.strip().split(None, 1)
        if not parts:
            return "Usage: /agent_wait <task_id> [timeout_seconds]"
        task_id = parts[0]
        timeout = int(parts[1]) if len(parts) > 1 else 60
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Subagent service is not available."
        task = await orchestrator.wait_for_task(
            task_id, timeout_seconds=max(timeout, 1)
        )
        if task is None or task.requester_session_id != session_id:
            return f"Task {task_id} not found."
        return self._format_background_task(task)

    async def _cmd_stop(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        router = self._get_router()
        if router is None:
            return "Router not available."
        runner = router._runner
        if runner is None:
            return "No active session runner."

        tracker = runner.run_tracker
        # Read tracker state only to choose the reply text. The stop *action*
        # is decoupled: it goes through the event bus (AgentStopRequested), so
        # any component can request a stop without reaching into router
        # internals. publish_event runs the router's priority-0 handler inline,
        # so the stop_event is set before this returns.
        run = tracker.get(session_id)
        if run is None:
            return "No active agent run for this session."
        if run.task.done():
            tracker.finish(session_id)
            return "Agent already finished."

        await self.api.publish_event(
            AgentStopRequested(
                payload=AgentStopPayload(session_id=session_id),
                source="builtin_commands.stop",
            )
        )
        _logger.info(
            "cmd.stop",
            session_id=session_id,
            platform=inbound.platform,
            chat_id=inbound.chat_id,
        )
        return "Agent stopped."

    def _get_router(self) -> Any:
        return getattr(self.api, "message_router", None)

    # ── Identity Tool ──────────────────────────────────

    def _register_identity_tool(self) -> None:
        self.api.register_tool(
            "identity_manage",
            (
                "Manage person/account identity records. Use this to create "
                "persons, link cross-channel accounts to the same person, "
                "unlink accounts, or query the current identity state. "
                "Requires admin privileges."
            ),
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "observations", "create", "link", "unlink"],
                        "description": (
                            "list: show all persons + linked accounts. "
                            "observations: show recently seen account_keys. "
                            "create: create or update a person. "
                            "link: bind an account_key to a person_id. "
                            "unlink: remove an account binding."
                        ),
                    },
                    "person_id": {
                        "type": "string",
                        "description": (
                            "Person identifier (for create/link). "
                            "Use a stable lowercase id like 'owner', 'alice'."
                        ),
                    },
                    "display_name": {
                        "type": "string",
                        "description": "Human-readable name (for create).",
                    },
                    "account_key": {
                        "type": "string",
                        "description": (
                            "Canonical account key in the format "
                            "'{channel}:user:{platform_user_id}', e.g. "
                            "'milky:user:123456'. Required for link/unlink."
                        ),
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            self._tool_identity_manage,
            requires_admin=True,
        )

    async def _tool_identity_manage(
        self,
        action: str,
        person_id: str = "",
        display_name: str = "",
        account_key: str = "",
    ) -> str:
        import json

        result = await self.api.identity_manage(
            action,
            person_id=person_id,
            display_name=display_name,
            account_key=account_key,
        )
        return json.dumps(result, ensure_ascii=False, sort_keys=True)


def _format_session_key_kind(kind: str) -> str:
    if kind == "typed":
        return "typed"
    if kind == "typed-derived":
        return "typed derived"
    if kind == "legacy":
        return "legacy"
    if kind == "legacy-derived":
        return "legacy derived"
    return "invalid"


def _chat_type_from_session_context(ctx: Any) -> str:
    address = _typed_address_from_session_context(ctx)
    return address.target_type if address is not None else ""


def _chat_type_from_inbound(inbound: InboundMessage) -> str:
    address = _address_from_inbound(inbound)
    return address.target_type if address.is_typed else ""
