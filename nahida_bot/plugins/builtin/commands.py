"""Builtin commands plugin — commands, workspace tools, exec, web_fetch, plan."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

import structlog

from nahida_bot.plugins.base import (
    Attachment,
    BotAPI,
    InboundMessage,
    OutboundMessage,
    Plugin,
    PluginManifest,
)
from nahida_bot.plugins.builtin.tools.history import HistoryTools
from nahida_bot.plugins.builtin.tools.memory import MemoryTools
from nahida_bot.plugins.builtin.tools.web_fetch import WebFetchTools
from nahida_bot.plugins.builtin.tools.workspace import WorkspaceTools
from nahida_bot.plugins.tooling import register_tool_definitions

from nahida_bot.core.chat_address import (
    ChatAddress,
    SessionKey,
    classify_session_key,
)
from nahida_bot.core.context import current_agent_run, current_session
from nahida_bot.core.events import AgentStopPayload, AgentStopRequested
from nahida_bot.core.runtime_settings import (
    REASONING_EFFORTS,
    runtime_settings_from_meta,
)
from nahida_bot.gateway.services.desktop_control import (
    MAX_DESKTOP_EXEC_ARGS,
    MAX_DESKTOP_EXEC_ARG_CHARS,
    MAX_DESKTOP_FILE_READ_BYTES,
    MAX_DESKTOP_PATH_CHARS,
    MAX_DESKTOP_PROGRAM_CHARS,
)

_logger = structlog.get_logger(__name__)

_MAX_EXEC_OUTPUT = 50_000
_MAX_EXEC_TIMEOUT = 120
_MAX_DESKTOP_TOOL_RESULT_CHARS = 70_000
_PLAN_PATH = ".agent/plan.json"


def _desktop_tool_error(code: str, message: str) -> str:
    return _bounded_desktop_tool_json(
        {"ok": False, "error": {"code": code or "failed", "message": message}}
    )


def _bounded_desktop_tool_json(value: dict[str, Any]) -> str:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        serialized = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "invalid_desktop_response",
                    "message": "Desktop returned a non-serializable response",
                },
            },
            sort_keys=True,
        )
    if len(serialized) <= _MAX_DESKTOP_TOOL_RESULT_CHARS:
        return serialized

    preview_chars = _MAX_DESKTOP_TOOL_RESULT_CHARS // 2
    while preview_chars > 0:
        bounded = json.dumps(
            {
                "ok": bool(value.get("ok")),
                "result": {
                    "truncated": True,
                    "preview": serialized[:preview_chars],
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if len(bounded) <= _MAX_DESKTOP_TOOL_RESULT_CHARS:
            return bounded
        preview_chars //= 2
    return _desktop_tool_error(
        "response_too_large", "Desktop response exceeded the output limit"
    )


class BuiltinCommandsPlugin(Plugin):
    """Registers core commands and built-in tools."""

    def __init__(self, api: BotAPI, manifest: PluginManifest) -> None:
        super().__init__(api, manifest)
        self._history_tools = HistoryTools(api)
        self._memory_tools = MemoryTools(api)
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
            "model", self._cmd_model, description="List or switch model (/model [name])"
        )
        self.api.register_command(
            "reasoning",
            self._cmd_reasoning,
            description=(
                "Show or change reasoning settings "
                "(/reasoning on|off|effort <level>|reset)"
            ),
            aliases=["think"],
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
        )

    def _register_workspace_tools(self) -> None:
        register_tool_definitions(self.api, self._workspace_tools.definitions())

    def _register_attachment_tools(self) -> None:
        self.api.register_tool(
            "send_local_attachment",
            "Send a local workspace file to the current chat as an attachment. "
            "Use this for images, documents, audio, or video files that already "
            "exist in the active workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the local file. By default this must be "
                            "relative to the active workspace. Absolute paths require "
                            "the builtin-commands allow_external_attachment_paths config."
                        ),
                    },
                    "attachment_type": {
                        "type": "string",
                        "enum": ["auto", "photo", "document", "audio", "video"],
                        "description": (
                            "Attachment type. Use auto to infer from the file MIME type."
                        ),
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption sent with the attachment.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename shown by the platform.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            self._tool_send_local_attachment,
        )

    def _register_memory_tools(self) -> None:
        register_tool_definitions(self.api, self._memory_tools.definitions())

    # ── Cross-session history & chat lookup ────────────────

    def _register_history_tools(self) -> None:
        register_tool_definitions(self.api, self._history_tools.definitions())

    async def _tool_read_chat_history(self, **arguments: Any) -> str:
        return await self._history_tools.read(**arguments)

    async def _tool_search_chat_history(
        self,
        query: str,
        chat_address: str = "",
        role: str = "",
        limit: int = 20,
    ) -> str:
        return await self._history_tools.search(
            query=query,
            chat_address=chat_address,
            role=role,
            limit=limit,
        )

    async def _tool_find_chat(self, name: str, platform: str = "") -> str:
        return await self._history_tools.find_chat(name=name, platform=platform)

    @staticmethod
    def _base_chat_key(session_id: str) -> str:
        return HistoryTools.base_chat_key(session_id)

    async def _resolve_chat_names(self, session_ids: list[str]) -> dict[str, str]:
        return await self._history_tools.resolve_chat_names(session_ids)

    @staticmethod
    def _sanitize_turn_for_model(content: str) -> str:
        return HistoryTools.sanitize_turn(content)

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

    @staticmethod
    def _is_private_ip(ip_str: str) -> bool:
        return WebFetchTools.is_disallowed_ip(ip_str)

    @staticmethod
    def _resolve_host(hostname: str) -> str | None:
        addresses = WebFetchTools.resolve_host(hostname)
        return addresses[0] if addresses else None

    @staticmethod
    def _html_to_markdown(html_content: str) -> str:
        return WebFetchTools.html_to_markdown(html_content)

    async def _tool_web_fetch(self, url: str, max_length: int = 10000) -> str:
        return await self._web_fetch_tools.fetch(url, max_length)

    # ── plan Tool ──────────────────────────────────────────

    def _register_plan_tool(self) -> None:
        self.api.register_tool(
            "plan",
            "Create and manage a task plan for structured work. "
            "Actions: create, list, update, add, remove, clear.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "update", "add", "remove", "clear"],
                        "description": "The action to perform on the plan.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Plan title (used with 'create').",
                    },
                    "tasks": {
                        "type": "array",
                        "description": "Tasks for 'create' or 'add'. Each has title and optional detail.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "detail": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "Task ID for 'update' or 'remove'.",
                    },
                    "status": {
                        "type": "string",
                        "description": "New status for 'update': pending, in_progress, completed, failed.",
                    },
                    "detail": {
                        "type": "string",
                        "description": "New detail text for 'update'.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            self._tool_plan,
        )

    async def _load_plan_data(self) -> dict[str, Any] | None:
        try:
            raw = await self.api.workspace_read(_PLAN_PATH)
            return json.loads(raw)
        except Exception:
            return None

    async def _save_plan_data(self, data: dict[str, Any]) -> None:
        await self.api.workspace_write(
            _PLAN_PATH, json.dumps(data, ensure_ascii=False, indent=2)
        )

    async def _read_workspace_text_or_empty(self, path: str) -> str:
        return await self._memory_tools.read_workspace_text_or_empty(path)

    async def _tool_memory_read(
        self,
        query: str = "",
        max_length: int = 10000,
    ) -> str:
        return await self._memory_tools.read(query=query, max_length=max_length)

    async def _tool_memory_write(
        self,
        content: str,
        title: str = "",
        kind: str = "fact",
        audience: str = "current",
        sensitivity: str = "public",
        portable: bool = True,
    ) -> str:
        return await self._memory_tools.write(
            content=content,
            title=title,
            kind=kind,
            audience=audience,
            sensitivity=sensitivity,
            portable=portable,
        )

    async def _tool_memory_update(
        self,
        item_id: str,
        content: str,
        title: str = "",
        kind: str = "",
        audience: str = "",
        target_scope_type: str = "",
        target_scope_id: str = "",
        sensitivity: str = "",
        portable: bool | None = None,
    ) -> str:
        return await self._memory_tools.update(
            item_id=item_id,
            content=content,
            title=title,
            kind=kind,
            audience=audience,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            sensitivity=sensitivity,
            portable=portable,
        )

    async def _tool_memory_archive(self, item_id: str, reason: str) -> str:
        return await self._memory_tools.archive(item_id=item_id, reason=reason)

    @staticmethod
    def _format_plan(data: dict[str, Any]) -> str:
        lines = [f"Plan: {data.get('title', 'Untitled')}"]
        tasks = data.get("tasks", [])
        if not tasks:
            lines.append("  (no tasks)")
        for t in tasks:
            status_marker = {
                "pending": "[ ]",
                "in_progress": "[~]",
                "completed": "[x]",
                "failed": "[!]",
            }.get(t.get("status", "pending"), "[ ]")
            line = f"  {t['id']}. {status_marker} {t['title']}"
            if t.get("detail"):
                line += f" — {t['detail']}"
            lines.append(line)
        return "\n".join(lines)

    async def _tool_plan(
        self,
        action: str,
        title: str = "",
        tasks: list[dict[str, str]] | None = None,
        task_id: int | None = None,
        status: str = "",
        detail: str = "",
    ) -> str:
        _logger.debug("tool.plan", action=action)

        if action == "create":
            task_list = tasks or []
            new_plan: dict[str, Any] = {
                "title": title or "Untitled Plan",
                "tasks": [
                    {
                        "id": i + 1,
                        "title": t["title"],
                        "status": "pending",
                        "detail": t.get("detail", ""),
                    }
                    for i, t in enumerate(task_list)
                ],
            }
            await self._save_plan_data(new_plan)
            return f"Plan created.\n{self._format_plan(new_plan)}"

        if action == "list":
            plan_data = await self._load_plan_data()
            if plan_data is None:
                return "No plan exists. Use action='create' to start one."
            return self._format_plan(plan_data)

        if action == "add":
            plan_data = await self._load_plan_data()
            if plan_data is None:
                return "No plan exists. Use action='create' to start one."
            current_tasks: list[dict[str, Any]] = plan_data.get("tasks", [])
            next_id = (max(t["id"] for t in current_tasks) + 1) if current_tasks else 1
            for t in tasks or []:
                current_tasks.append(
                    {
                        "id": next_id,
                        "title": t["title"],
                        "status": "pending",
                        "detail": t.get("detail", ""),
                    }
                )
                next_id += 1
            plan_data["tasks"] = current_tasks
            await self._save_plan_data(plan_data)
            return f"Tasks added.\n{self._format_plan(plan_data)}"

        if action == "update":
            plan_data = await self._load_plan_data()
            if plan_data is None:
                return "No plan exists."
            if task_id is None:
                return "Error: task_id is required for update."
            valid_statuses = {"pending", "in_progress", "completed", "failed"}
            if status and status not in valid_statuses:
                return f"Error: Invalid status '{status}'. Must be one of: {', '.join(sorted(valid_statuses))}"
            found = False
            for t in plan_data.get("tasks", []):
                if t["id"] == task_id:
                    if status:
                        t["status"] = status
                    if detail:
                        t["detail"] = detail
                    found = True
                    break
            if not found:
                return f"Error: Task {task_id} not found."
            await self._save_plan_data(plan_data)
            return f"Task {task_id} updated.\n{self._format_plan(plan_data)}"

        if action == "remove":
            plan_data = await self._load_plan_data()
            if plan_data is None:
                return "No plan exists."
            if task_id is None:
                return "Error: task_id is required for remove."
            original_len = len(plan_data.get("tasks", []))
            plan_data["tasks"] = [
                t for t in plan_data.get("tasks", []) if t["id"] != task_id
            ]
            # Renumber remaining tasks
            for i, t in enumerate(plan_data["tasks"]):
                t["id"] = i + 1
            if len(plan_data["tasks"]) == original_len:
                return f"Error: Task {task_id} not found."
            await self._save_plan_data(plan_data)
            return f"Task removed.\n{self._format_plan(plan_data)}"

        if action == "clear":
            try:
                await self.api.workspace_write(_PLAN_PATH, "")
            except Exception:
                pass
            return "Plan cleared."

        return f"Error: Unknown action '{action}'."

    # ── Cron Tools ─────────────────────────────────────────

    def _register_cron_tools(self) -> None:
        self.api.register_tool(
            "cron_create",
            "Create a scheduled task that runs a prompt once, repeatedly at a fixed interval, or by a 5-field cron expression.",
            {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The text prompt to execute when the task fires.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["once", "interval", "cron"],
                        "description": "'once' fires at a specific datetime; 'interval' fires repeatedly; 'cron' uses a 5-field cron expression.",
                    },
                    "fire_at": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime for 'once' mode, e.g. '2025-06-15T09:00:00'. "
                            "If no timezone is given, UTC is assumed."
                        ),
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": "Seconds between fires for 'interval' mode. Minimum 60.",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "5-field cron expression for 'cron' mode, e.g. '0 9 * * 1-5'.",
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": "Max number of fires for interval or cron mode. Omit for infinite.",
                    },
                    "session_mode": {
                        "type": "string",
                        "enum": ["main", "isolated", "fresh"],
                        "description": (
                            "'main' (default) uses the chat session. "
                            "'isolated' reuses one private session per cron job. "
                            "'fresh' creates a new session for each fire."
                        ),
                    },
                },
                "required": ["prompt", "mode"],
                "additionalProperties": False,
            },
            self._tool_cron_create,
        )
        self.api.register_tool(
            "cron_list",
            "List all active scheduled tasks for the current chat.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            self._tool_cron_list,
        )
        self.api.register_tool(
            "cron_cancel",
            "Cancel a scheduled task by its job ID.",
            {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job ID returned by cron_create or shown in cron_list.",
                    },
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            self._tool_cron_cancel,
        )
        self.api.register_tool(
            "cron_update",
            "Update an active scheduled task's prompt, schedule, or max run count.",
            {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job ID returned by cron_create or shown in cron_list.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Replacement prompt to execute when the task fires.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["once", "interval", "cron"],
                        "description": "Switch the task to one-shot, interval, or cron mode.",
                    },
                    "fire_at": {
                        "type": "string",
                        "description": "ISO 8601 datetime for one-shot mode. If no timezone is given, UTC is assumed.",
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": "Seconds between fires for interval mode. Minimum 60.",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "5-field cron expression for cron mode, e.g. '0 9 * * 1-5'.",
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": "Max number of successful fires for interval or cron mode.",
                    },
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            self._tool_cron_update,
        )
        self.api.register_tool(
            "cron_delete",
            "Permanently delete a scheduled task by its job ID.",
            {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "The job ID returned by cron_create or shown in cron_list.",
                    },
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
            self._tool_cron_delete,
        )

    def _get_scheduler(self) -> Any:
        """Access the SchedulerService exposed by the plugin API."""
        return self.api.scheduler_service

    # ── Agent Orchestration Tools ────────────────────────

    def _register_agent_tools(self) -> None:
        self.api.register_tool(
            "agent_spawn",
            "Start a one-off background subagent task in an isolated child session.",
            {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Concrete delegated task for the subagent.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Short display label for the task.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Temporary task-specific instructions.",
                    },
                    "context_mode": {
                        "type": "string",
                        "enum": ["isolated", "summary", "fork"],
                        "description": "How much parent context to pass.",
                    },
                    "provider_id": {
                        "type": "string",
                        "description": "Optional provider id for the child agent.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model or provider/model for the child agent.",
                    },
                    "reasoning_effort": {
                        "type": "string",
                        "enum": sorted(REASONING_EFFORTS),
                        "description": "Optional reasoning effort override for the child agent.",
                    },
                    "handoff_summary": {
                        "type": "string",
                        "description": "Brief parent context summary for summary mode.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Maximum subagent runtime in seconds.",
                    },
                    "notify": {
                        "type": "string",
                        "enum": ["done_only", "silent"],
                        "description": "Whether to write a completion event to the parent session.",
                    },
                    "tool_denylist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Extra tool names to hide from the child.",
                    },
                    "tool_allowlist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "If set, only these tool names are visible to the child.",
                    },
                },
                "required": ["task"],
                "additionalProperties": False,
            },
            self._tool_agent_spawn,
        )
        self.api.register_tool(
            "agent_wait",
            "Wait for a subagent task result. Timeout does not cancel the task.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
            self._tool_agent_wait,
        )
        # Note: ``agent_yield`` was removed (issue #41). It previously aliased
        # ``agent_wait`` while implying a "yield current turn, continue in the
        # background" semantic that the runtime never implemented. Real
        # continuation remains a future feature; until then ``agent_wait`` is
        # the only blocking-wait tool and the system denylist still pins the
        # name so stale prompts cannot resurrect it from a child run.
        self.api.register_tool(
            "agent_list",
            "List subagent tasks created by the current session.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": [],
                "additionalProperties": False,
            },
            self._tool_agent_list,
        )
        self.api.register_tool(
            "agent_stop",
            "Cancel a subagent task created by the current session.",
            {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            self._tool_agent_stop,
        )

    def _get_orchestrator(self) -> Any:
        return getattr(self.api, "orchestration_service", None)

    async def _tool_agent_spawn(
        self,
        task: str,
        label: str = "",
        instructions: str = "",
        context_mode: str = "isolated",
        provider_id: str = "",
        model: str = "",
        reasoning_effort: str = "",
        handoff_summary: str = "",
        timeout_seconds: int | None = None,
        notify: str = "done_only",
        tool_allowlist: list[str] | None = None,
        tool_denylist: list[str] | None = None,
    ) -> str:
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        try:
            from nahida_bot.agent.orchestration import SubagentSpec

            spec = SubagentSpec(
                task=task,
                label=label or None,
                instructions=instructions or None,
                context_mode=context_mode,  # type: ignore[arg-type]
                handoff_summary=handoff_summary or None,
                provider_id=provider_id or None,
                model=model or None,
                reasoning_effort=reasoning_effort or None,
                timeout_seconds=timeout_seconds,
                tool_allowlist=tuple(tool_allowlist or ()),
                tool_denylist=tuple(tool_denylist or ()),
                notify_policy=notify,  # type: ignore[arg-type]
            )
            bg_task = await orchestrator.spawn_subagent(spec)
        except Exception as e:
            return f"Error spawning subagent: {e}"

        return json.dumps(
            {
                "task_id": bg_task.task_id,
                "child_session_id": bg_task.child_session_id,
                "status": bg_task.status.value,
                "title": bg_task.title,
            },
            ensure_ascii=False,
        )

    async def _tool_agent_wait(self, task_id: str, timeout_seconds: int = 30) -> str:
        requester_session_id = self._current_requester_session_id()
        if requester_session_id is None:
            return "Error: No active session context."
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        task = await orchestrator.wait_for_task(
            task_id,
            timeout_seconds=max(timeout_seconds, 0),
        )
        if task is None or task.requester_session_id != requester_session_id:
            return f"Task {task_id} not found."
        return self._format_background_task(task)

    async def _tool_agent_list(self, limit: int = 20) -> str:
        requester_session_id = self._current_requester_session_id()
        if requester_session_id is None:
            return "Error: No active session context."
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        tasks = await orchestrator.list_tasks(requester_session_id, limit=max(limit, 1))
        if not tasks:
            return "No subagent tasks for this session."
        return "\n".join(self._format_background_task(task) for task in tasks)

    async def _tool_agent_stop(self, task_id: str) -> str:
        requester_session_id = self._current_requester_session_id()
        if requester_session_id is None:
            return "Error: No active session context."
        orchestrator = self._get_orchestrator()
        if orchestrator is None:
            return "Error: Agent orchestration service is not available."
        task = await orchestrator.stop_task(requester_session_id, task_id)
        if task is None:
            return f"Task {task_id} not found or not owned by this session."
        return self._format_background_task(task)

    @staticmethod
    def _current_requester_session_id() -> str | None:
        from nahida_bot.core.context import current_agent_run

        run_ctx = current_agent_run.get()
        if run_ctx is not None:
            return run_ctx.requester_session_id
        ctx = current_session.get()
        return ctx.session_id if ctx is not None else None

    @staticmethod
    def _format_background_task(task: Any) -> str:
        lines = [
            f"{task.task_id}: {task.status.value} — {task.title}",
            f"  child_session: {task.child_session_id or '(none)'}",
        ]
        if task.summary:
            lines.append(f"  summary: {task.summary[:1000]}")
        if task.error:
            lines.append(f"  error: {task.error[:1000]}")
        return "\n".join(lines)

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
        self.api.register_tool(
            "desktop_exec",
            (
                "Run a program on the current actor's Desktop. The same call is "
                "adjudicated locally by Desktop mode: scoped mode interprets program "
                "and cwd within its configured policy, while Full Access mode permits "
                "full paths. This tool cannot select a node or capability. Available "
                "in normal chat and scheduled CRON runs."
            ),
            {
                "type": "object",
                "properties": {
                    "program": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_DESKTOP_PROGRAM_CHARS,
                        "description": "Program identifier or path, interpreted by Desktop mode.",
                    },
                    "args": {
                        "type": "array",
                        "maxItems": MAX_DESKTOP_EXEC_ARGS,
                        "items": {
                            "type": "string",
                            "maxLength": MAX_DESKTOP_EXEC_ARG_CHARS,
                        },
                        "default": [],
                        "description": "Arguments passed to the approved profile.",
                    },
                    "cwd": {
                        "type": "string",
                        "maxLength": MAX_DESKTOP_PATH_CHARS,
                        "default": "",
                        "description": "Optional working directory interpreted by Desktop mode.",
                    },
                },
                "required": ["program"],
                "additionalProperties": False,
            },
            self._tool_desktop_exec,
            requires_admin=True,
        )
        self.api.register_tool(
            "desktop_file_read",
            (
                "Read a bounded byte range on the current actor's Desktop. The same "
                "call is adjudicated locally by Desktop mode: scoped mode applies its "
                "configured roots, while Full Access mode permits full paths. This "
                "tool cannot select a node or capability. Available in normal chat "
                "and scheduled CRON runs."
            ),
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_DESKTOP_PATH_CHARS,
                        "description": "File path interpreted by Desktop mode.",
                    },
                    "root_id": {
                        "type": "string",
                        "maxLength": 128,
                        "default": "",
                        "description": "Optional Desktop-configured root identifier.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Zero-based byte offset.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_DESKTOP_FILE_READ_BYTES,
                        "default": 65536,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            self._tool_desktop_file_read,
            requires_admin=True,
        )

    async def _tool_desktop_exec(
        self, program: str, args: list[str] | None = None, cwd: str = ""
    ) -> str:
        return await self._invoke_desktop_control(
            "exec",
            program=program,
            args=args or [],
            cwd=cwd,
        )

    async def _tool_desktop_file_read(
        self,
        path: str,
        root_id: str = "",
        offset: int = 0,
        max_bytes: int = 65536,
    ) -> str:
        return await self._invoke_desktop_control(
            "file_read",
            path=path,
            root_id=root_id,
            offset=offset,
            max_bytes=max_bytes,
        )

    async def _invoke_desktop_control(self, operation: str, **arguments: Any) -> str:
        run_ctx = current_agent_run.get()
        if run_ctx is not None and run_ctx.depth > 0:
            return _desktop_tool_error(
                "subagent_denied", "Desktop control is unavailable to subagents"
            )
        ctx = current_session.get()
        if ctx is None or not ctx.actor_account_key.strip():
            return _desktop_tool_error(
                "actor_unavailable", "trusted actor identity is unavailable"
            )
        service = getattr(self.api, "desktop_control_service", None)
        if service is None:
            return _desktop_tool_error(
                "service_unavailable", "Desktop control service is unavailable"
            )

        method = service.exec if operation == "exec" else service.file_read
        result = await method(
            **arguments,
            conversation_id=ctx.effective_conversation_id,
            actor_account_key=ctx.actor_account_key,
            caller=f"agent:{ctx.origin or 'chat'}:{ctx.session_id}",
        )
        if not result.ok:
            return _desktop_tool_error(result.error_code, result.error_message)
        return _bounded_desktop_tool_json({"ok": True, "result": result.payload})

    def _register_message_tool(self) -> None:
        self.api.register_tool(
            "message",
            (
                "Send a message to a chat on any registered platform. "
                "Use 'notify' delivery for one-time notifications that do not "
                "affect the target session's history. Use 'record' delivery to "
                "also write the message into the target session's conversation "
                "history, so the agent there can see it in context next time."
                "Note that your output text will be sent to the current session "
                "as well, so DO NOT use this tool to reply to the current "
                "session's message. Only use it to send messages to other sessions."
            ),
            {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Delivery target as 'platform:type:id' "
                            "(e.g. 'milky:group:20001', 'telegram:private:123456')."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send.",
                    },
                    "delivery": {
                        "type": "string",
                        "enum": ["notify", "record"],
                        "description": (
                            "Delivery mode. 'notify' (default) sends without "
                            "affecting the target session's history. 'record' "
                            "also writes into the target session's history so "
                            "the agent there sees it in context."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": (
                                        "Path to the file. Relative to workspace, "
                                        "or absolute if allowed by config."
                                    ),
                                },
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "auto",
                                        "photo",
                                        "document",
                                        "audio",
                                        "video",
                                    ],
                                    "description": (
                                        "Attachment type. 'auto' infers from file MIME type."
                                    ),
                                },
                                "caption": {
                                    "type": "string",
                                    "description": "Optional caption for the attachment.",
                                },
                            },
                            "required": ["path"],
                            "additionalProperties": False,
                        },
                        "description": "Optional files to send alongside the message.",
                    },
                },
                "required": ["target", "text"],
                "additionalProperties": False,
            },
            self._tool_message,
            requires_admin=True,
        )

    async def _tool_message(
        self,
        text: str,
        target: str = "",
        delivery: str = "notify",
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        if delivery not in ("notify", "record"):
            return "Error: delivery must be 'notify' or 'record'."

        if not target:
            return "Error: Provide a typed 'target' such as 'milky:group:20001'."
        try:
            address = ChatAddress.parse(target)
        except ValueError as exc:
            return f"Error: Invalid target format: {exc}"
        if not address.is_typed:
            return "Error: target must include a chat type, such as private or group."

        # Resolve attachments
        resolved_attachments: list[Attachment] = []
        if attachments:
            for item in attachments:
                raw_path = item.get("path", "")
                if not raw_path:
                    return "Error: Each attachment must have a 'path'."
                try:
                    file_path = self._resolve_attachment_path(raw_path)
                except ValueError as exc:
                    return f"Error: {exc}"
                if not file_path.is_file():
                    return f"Error: File does not exist: {raw_path}"

                attachment_type = item.get("type", "auto")
                if attachment_type not in (
                    "auto",
                    "photo",
                    "document",
                    "audio",
                    "video",
                ):
                    return (
                        "Error: attachment type must be one of: "
                        "auto, photo, document, audio, video."
                    )
                selected_type = (
                    self._infer_attachment_type(file_path)
                    if attachment_type == "auto"
                    else attachment_type
                )
                resolved_attachments.append(
                    Attachment(
                        type=selected_type,
                        path=str(file_path),
                        caption=item.get("caption", ""),
                    )
                )

        outbound = OutboundMessage(
            text=text,
            extra={"chat_address": address.chat_key},
            attachments=resolved_attachments,
        )
        message_id = await self.api.send_message(
            address.target_id,
            outbound,
            channel=address.channel,
        )
        delivery_metadata: dict[str, Any] = {
            "from_session": ctx.session_id,
            "from_platform": ctx.platform,
            "from_chat_id": ctx.chat_id,
            "from_user_id": ctx.user_id,
        }
        if ctx.chat_address is not None:
            delivery_metadata["from_chat_address"] = ctx.chat_address.chat_key
        if resolved_attachments:
            delivery_metadata["attachment_count"] = len(resolved_attachments)
        record_delivery = cast(
            Callable[..., Awaitable[Any]],
            getattr(self.api, "record_message_delivery", None),
        )
        if callable(record_delivery):
            await record_delivery(
                target=address,
                text=text,
                source="message_tool",
                delivery_mode=delivery,
                status="sent",
                message_id=message_id,
                metadata=delivery_metadata,
            )

        # Record in target session history if requested
        if delivery == "record":
            metadata: dict[str, Any] = {
                "from_session": ctx.session_id,
                "from_platform": ctx.platform,
                "from_chat_id": ctx.chat_id,
                "from_user_id": ctx.user_id,
            }
            if ctx.chat_address is not None:
                metadata["from_chat_address"] = ctx.chat_address.chat_key
            if resolved_attachments:
                metadata["attachment_count"] = len(resolved_attachments)
            await self.api.record_session_event(
                address.chat_key,
                text,
                source="cross_session_message",
                metadata=metadata,
            )

        display = address.chat_key
        parts = [f"Message sent to {display}"]
        if delivery == "record":
            parts.append("(recorded in target session history)")
        if message_id:
            parts[0] += f" (id: {message_id})"
        return ", ".join(parts)

    async def _tool_cron_create(
        self,
        prompt: str,
        mode: str,
        fire_at: str | None = None,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        max_runs: int | None = None,
        session_mode: str = "main",
    ) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Error: Scheduler is not available."

        if mode == "once":
            if not fire_at:
                return "Error: 'fire_at' is required for mode='once'."
            from datetime import UTC, datetime

            try:
                dt = datetime.fromisoformat(fire_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                fire_at = dt.astimezone(UTC).isoformat()
            except ValueError:
                return f"Error: Invalid datetime format: {fire_at}"
        elif mode == "interval":
            if not interval_seconds or interval_seconds < 60:
                return "Error: 'interval_seconds' must be >= 60 for mode='interval'."
            if max_runs is not None and max_runs <= 0:
                return "Error: 'max_runs' must be > 0 when provided."
        elif mode == "cron":
            if not cron_expression:
                return "Error: 'cron_expression' is required for mode='cron'."
        else:
            return f"Error: Invalid mode '{mode}'. Use 'once', 'interval', or 'cron'."

        try:
            address = _typed_address_from_session_context(ctx)
            if address is None:
                return "Error: Current chat does not have a typed delivery target."

            job = await scheduler.create_job(
                address=address,
                prompt=prompt,
                mode=mode,
                fire_at=fire_at if mode == "once" else None,
                interval_seconds=interval_seconds,
                cron_expression=cron_expression,
                max_runs=max_runs,
                workspace_id=ctx.workspace_id,
                session_mode=session_mode,
                created_by_user_id=ctx.user_id,
                created_from_session_id=ctx.session_id,
                created_from_chat_address=address.chat_key,
                # TODO(authz): sender_account_key inherits the *current turn's*
                # initiator.  In group chats this may be a conversation-joiner
                # anchor rather than an explicit @-mention — the permission model
                # for cron ownership in auto-join scenarios needs further thought.
                sender_account_key=ctx.sender_account_key,
            )
        except Exception as e:
            return f"Error creating scheduled task: {e}"

        # Format summary
        lines = [f"Scheduled task created (id: {job.job_id})"]
        if mode == "once":
            lines.append(f"  Mode: once at {job.next_fire_at}")
        elif mode == "cron":
            lines.append(f"  Mode: cron ({cron_expression})")
            if max_runs:
                lines.append(f"  Max runs: {max_runs}")
            else:
                lines.append("  Max runs: infinite")
        else:
            lines.append(f"  Mode: every {interval_seconds}s")
            if max_runs:
                lines.append(f"  Max runs: {max_runs}")
            else:
                lines.append("  Max runs: infinite")
        lines.append(f"  Next fire: {job.next_fire_at}")
        lines.append(f"  Session: {job.session_mode}")
        lines.append(f"  Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        return "\n".join(lines)

    async def _tool_cron_list(self) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Error: Scheduler is not available."

        address = _address_from_session_context(ctx)
        jobs = [
            job
            for job in await scheduler.list_jobs(address)
            if _job_visible_to_user(job, address, ctx.user_id)
        ]
        if not jobs:
            return "No active scheduled tasks for this chat."

        lines = [f"Active scheduled tasks ({len(jobs)}):"]
        for j in jobs:
            if j.mode == "once":
                schedule = f"once at {j.next_fire_at}"
            elif j.mode == "cron":
                schedule = f"cron ({j.cron_expression})"
            else:
                schedule = f"every {j.interval_seconds}s"
            preview = j.prompt[:60] + ("..." if len(j.prompt) > 60 else "")
            lines.append(
                f"  {j.job_id}: [{j.mode}/{j.session_mode}] {schedule} — {preview}"
            )
            lines.append(f"    runs: {j.run_count}, next: {j.next_fire_at}")
            if j.failure_count:
                lines.append(
                    f"    failures: {j.failure_count}, last error: {j.last_error}"
                )
        return "\n".join(lines)

    async def _tool_cron_cancel(self, job_id: str) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Error: Scheduler is not available."

        # Verify ownership
        job = await scheduler.get_job(job_id)
        if job is None:
            return f"Error: Job '{job_id}' not found."
        address = _typed_address_from_session_context(ctx)
        if address is None or not _job_visible_to_user(job, address, ctx.user_id):
            return f"Error: Job '{job_id}' does not belong to this chat."

        cancelled = await scheduler.cancel_job(job_id)
        if cancelled:
            return f"Cancelled task {job_id}."
        return f"Task {job_id} is already inactive or completed."

    async def _tool_cron_update(
        self,
        job_id: str,
        prompt: str | None = None,
        mode: str | None = None,
        fire_at: str | None = None,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        max_runs: int | None = None,
    ) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Error: Scheduler is not available."

        job = await scheduler.get_job(job_id)
        if job is None:
            return f"Error: Job '{job_id}' not found."
        address = _typed_address_from_session_context(ctx)
        if address is None or not _job_visible_to_user(job, address, ctx.user_id):
            return f"Error: Job '{job_id}' does not belong to this chat."

        if mode is not None and mode not in {"once", "interval", "cron"}:
            return f"Error: Invalid mode '{mode}'. Use 'once', 'interval', or 'cron'."
        if interval_seconds is not None and interval_seconds < 60:
            return "Error: 'interval_seconds' must be >= 60 for mode='interval'."
        if max_runs is not None and max_runs <= 0:
            return "Error: 'max_runs' must be > 0 when provided."

        try:
            updated = await scheduler.update_job(
                job_id,
                prompt=prompt,
                mode=mode,
                fire_at=fire_at,
                interval_seconds=interval_seconds,
                cron_expression=cron_expression,
                max_runs=max_runs,
            )
        except Exception as e:
            return f"Error updating scheduled task: {e}"

        lines = [f"Updated task {updated.job_id}."]
        if updated.mode == "once":
            lines.append(f"  Mode: once at {updated.next_fire_at}")
        elif updated.mode == "cron":
            lines.append(f"  Mode: cron ({updated.cron_expression})")
            lines.append(
                f"  Max runs: {updated.max_runs if updated.max_runs else 'infinite'}"
            )
        else:
            lines.append(f"  Mode: every {updated.interval_seconds}s")
            lines.append(
                f"  Max runs: {updated.max_runs if updated.max_runs else 'infinite'}"
            )
        lines.append(f"  Next fire: {updated.next_fire_at}")
        lines.append(
            f"  Prompt: {updated.prompt[:100]}{'...' if len(updated.prompt) > 100 else ''}"
        )
        return "\n".join(lines)

    async def _tool_cron_delete(self, job_id: str) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Error: Scheduler is not available."

        job = await scheduler.get_job(job_id)
        if job is None:
            return f"Error: Job '{job_id}' not found."
        address = _typed_address_from_session_context(ctx)
        if address is None or not _job_visible_to_user(job, address, ctx.user_id):
            return f"Error: Job '{job_id}' does not belong to this chat."

        deleted = await scheduler.delete_job(job_id)
        if deleted:
            return f"Deleted task {job_id}."
        return f"Task {job_id} was already deleted."

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
        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Scheduler is not available."
        address = _address_from_inbound(inbound)
        jobs = [
            job
            for job in await scheduler.list_jobs(address)
            if _job_visible_to_user(job, address, inbound.user_id)
        ]
        if not jobs:
            return "No scheduled tasks for this chat."
        lines = []
        for j in jobs:
            status_tag = "active" if j.is_active else "inactive"
            next_at = f", next: {j.next_fire_at}" if j.is_active else ""
            lines.append(
                f"  {j.job_id}  [{j.mode}] {status_tag}  runs: {j.run_count}{next_at}"
            )
            prompt_preview = j.prompt[:80] + ("..." if len(j.prompt) > 80 else "")
            lines.append(f"    {prompt_preview}")
        return "\n".join(lines)

    async def _cron_cancel(self, job_id: str, inbound: InboundMessage) -> str:
        if not job_id:
            return "Usage: /cron cancel <job_id>"
        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Scheduler is not available."
        job = await scheduler.get_job(job_id)
        address = _address_from_inbound(inbound)
        if (
            job is None
            or not address.is_typed
            or not _job_visible_to_user(job, address, inbound.user_id)
        ):
            return f"Task '{job_id}' not found."
        cancelled = await scheduler.cancel_job(job_id)
        if cancelled:
            return f"Cancelled task {job_id}."
        return f"Task {job_id} is already inactive."

    async def _cron_delete(self, job_id: str, inbound: InboundMessage) -> str:
        if not job_id:
            return "Usage: /cron delete <job_id>"
        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Scheduler is not available."
        job = await scheduler.get_job(job_id)
        address = _address_from_inbound(inbound)
        if (
            job is None
            or not address.is_typed
            or not _job_visible_to_user(job, address, inbound.user_id)
        ):
            return f"Task '{job_id}' not found."
        deleted = await scheduler.delete_job(job_id)
        if deleted:
            return f"Deleted task {job_id}."
        return f"Task {job_id} was already deleted."

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

    async def _tool_workspace_read(self, path: str) -> str:
        """Read a text file from the current workspace."""
        return await self._workspace_tools.read(path)

    async def _tool_workspace_write(self, path: str, content: str) -> str:
        """Write a text file to the current workspace."""
        return await self._workspace_tools.write(path, content)

    async def _tool_send_local_attachment(
        self,
        path: str,
        attachment_type: str = "auto",
        caption: str = "",
        filename: str = "",
    ) -> str:
        """Send a workspace file to the current chat as an attachment."""
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        if attachment_type not in {"auto", "photo", "document", "audio", "video"}:
            return (
                "Error: attachment_type must be one of: "
                "auto, photo, document, audio, video."
            )

        try:
            file_path = self._resolve_attachment_path(path)
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: Invalid attachment path: {exc}"

        if not file_path.is_file():
            return f"Error: File does not exist: {path}"

        selected_type = (
            self._infer_attachment_type(file_path)
            if attachment_type == "auto"
            else attachment_type
        )
        extra: dict[str, Any] = {}
        address = _typed_address_from_session_context(ctx)
        if address is not None:
            extra["chat_address"] = address.chat_key
        message_id = await self.api.send_message(
            ctx.chat_id,
            OutboundMessage(
                text="",
                extra=extra,
                attachments=[
                    Attachment(
                        type=selected_type,
                        path=str(file_path),
                        filename=filename or file_path.name,
                        caption=caption,
                    )
                ],
            ),
            channel=ctx.platform,
        )
        return f"Attachment sent: {message_id}" if message_id else "Attachment sent."

    def _resolve_attachment_path(self, path: str) -> Path:
        raw_path = Path(path).expanduser()
        if raw_path.is_absolute():
            if not self._allow_external_attachment_paths():
                raise ValueError(
                    "Absolute attachment paths are disabled. Use a workspace-relative "
                    "path or enable builtin-commands.allow_external_attachment_paths."
                )
            resolved = raw_path.resolve(strict=False)
            self._validate_external_attachment_path(resolved)
            return resolved

        resolved_workspace_path = self.api.resolve_workspace_path(path)
        if not resolved_workspace_path:
            raise ValueError("Workspace is not available.")
        return Path(resolved_workspace_path)

    def _allow_external_attachment_paths(self) -> bool:
        return bool(self.manifest.config.get("allow_external_attachment_paths", False))

    def _validate_external_attachment_path(self, path: Path) -> None:
        raw_roots = self.manifest.config.get("external_attachment_roots", [])
        if not raw_roots:
            return
        if not isinstance(raw_roots, list):
            raise ValueError("external_attachment_roots must be a list of paths.")

        allowed_roots = [
            Path(str(root)).expanduser().resolve(strict=False)
            for root in raw_roots
            if str(root).strip()
        ]
        if not allowed_roots:
            return
        for root in allowed_roots:
            try:
                path.relative_to(root)
                return
            except ValueError:
                continue
        roots = ", ".join(str(root) for root in allowed_roots)
        raise ValueError(f"Attachment path is outside allowed external roots: {roots}")

    @staticmethod
    def _infer_attachment_type(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type:
            if mime_type.startswith("image/"):
                return "photo"
            if mime_type.startswith("audio/"):
                return "audio"
            if mime_type.startswith("video/"):
                return "video"
        return "document"


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


def _address_from_inbound(inbound: InboundMessage) -> ChatAddress:
    chat_type = ""
    if inbound.chat_context and inbound.chat_context.chat_type:
        chat_type = inbound.chat_context.chat_type
    elif inbound.message_context and inbound.message_context.chat_type:
        chat_type = inbound.message_context.chat_type
    return ChatAddress.from_inbound(
        inbound.platform,
        inbound.chat_id,
        is_group=inbound.is_group,
        chat_type=chat_type,
    )


def _address_from_session_context(ctx: Any) -> ChatAddress:
    address = getattr(ctx, "chat_address", None)
    if isinstance(address, ChatAddress):
        return address
    return ChatAddress.from_inbound(
        str(getattr(ctx, "platform", "")),
        str(getattr(ctx, "chat_id", "")),
    )


def _typed_address_from_session_context(ctx: Any) -> ChatAddress | None:
    address = _address_from_session_context(ctx)
    return address if address.is_typed else None


def _job_matches_address(job: Any, address: ChatAddress) -> bool:
    return (
        address.is_typed
        and job.platform == address.channel
        and job.chat_id == address.target_id
        and job.chat_type == address.target_type
    )


def _job_visible_to_user(job: Any, address: ChatAddress, user_id: str) -> bool:
    if not _job_matches_address(job, address):
        return False
    # TODO(cron-group-management): Revisit group-chat ownership UX. Per-creator
    # filtering prevents accidental edits, but group admins may need a controlled
    # way to list or manage all jobs in the group.
    owner = str(getattr(job, "created_by_user_id", "") or "")
    if not owner:
        return True
    return bool(user_id) and owner == user_id
