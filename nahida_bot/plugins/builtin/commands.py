"""Builtin commands plugin — commands, workspace tools, exec, web_fetch, plan."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from typing import Any

import httpx
import structlog
from markdownify import markdownify as md
from readability import Document

from nahida_bot.agent.memory.markdown import (
    MEMORY_FILE,
    MEMORY_SUMMARY_FILE,
    MAX_TOOL_READ_CHARS,
    append_daily_memory,
    append_long_term_memory,
    daily_memory_path,
    filter_memory_text,
    recent_daily_memory_paths,
    validate_memory_content,
)
from nahida_bot.plugins.base import InboundMessage, Plugin

from nahida_bot.core.context import current_session

_logger = structlog.get_logger(__name__)

_MAX_EXEC_OUTPUT = 50_000
_MAX_EXEC_TIMEOUT = 120
_WEB_FETCH_TIMEOUT = 30
_WEB_FETCH_MAX_BODY = 5 * 1024 * 1024
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]
_PLAN_PATH = ".agent/plan.json"


class BuiltinCommandsPlugin(Plugin):
    """Registers core commands and built-in tools."""

    async def on_load(self) -> None:
        self._register_commands()
        self._register_workspace_tools()
        self._register_memory_tools()
        self._register_exec_tool()
        self._register_web_fetch_tool()
        self._register_plan_tool()
        self._register_cron_tools()
        self._register_agent_tools()

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
            "model", self._cmd_model, description="List or switch model (/model [name])"
        )
        self.api.register_command(
            "help", self._cmd_help, description="List available commands"
        )
        self.api.register_command(
            "memory",
            self._cmd_memory,
            description="Search or store durable memory",
        )

    def _register_workspace_tools(self) -> None:
        self.api.register_tool(
            "workspace_read",
            "Read a UTF-8 text file from the active workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the active workspace.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            self._tool_workspace_read,
        )
        self.api.register_tool(
            "workspace_write",
            "Write UTF-8 text content to a file in the active workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the active workspace.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            self._tool_workspace_write,
        )

    def _register_memory_tools(self) -> None:
        self.api.register_tool(
            "memory_read",
            "Read workspace Markdown memory from MEMORY.md and recent daily notes. "
            "Use this before relying on remembered facts that are not already in context.",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional text to search for in memory lines.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of recent daily memory files to include. Default 3.",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum characters to return. Default 10000.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            self._tool_memory_read,
        )
        self.api.register_tool(
            "memory_write",
            "Append a concise note to workspace Markdown memory. Use only for durable "
            "preferences, decisions, project facts, or explicit user requests to remember.",
            {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Concise memory text to append.",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["daily", "long_term", "both"],
                        "description": "Where to write the memory. Default daily.",
                    },
                    "section": {
                        "type": "string",
                        "description": "Section title for long_term writes. Default Notes.",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            self._tool_memory_write,
        )

    # ── exec Tool ──────────────────────────────────────────

    def _register_exec_tool(self) -> None:
        self.api.register_tool(
            "exec",
            "Execute a shell command and return its stdout, stderr, and exit code.",
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
                        "description": "Working directory relative to workspace root.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            self._tool_exec,
        )

    async def _tool_exec(
        self,
        command: str,
        timeout: int = 30,
        working_dir: str = "",
    ) -> str:
        _logger.debug("tool.exec", command=command, timeout=timeout, cwd=working_dir)

        actual_timeout = min(max(timeout, 1), _MAX_EXEC_TIMEOUT)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir or None,
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

    # ── web_fetch Tool ─────────────────────────────────────

    def _register_web_fetch_tool(self) -> None:
        self.api.register_tool(
            "web_fetch",
            "Fetch a web page and return its main content as Markdown.",
            {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch (http or https).",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "Maximum content length in characters (default 10000).",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            self._tool_web_fetch,
        )

    @staticmethod
    def _is_private_ip(ip_str: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip_str)
            return any(addr in net for net in _PRIVATE_NETWORKS)
        except ValueError:
            return False

    @staticmethod
    def _resolve_host(hostname: str) -> str | None:
        try:
            results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            for _family, _type, _proto, _canon, sockaddr in results:
                ip = sockaddr[0]
                if isinstance(ip, str):
                    return ip
        except (socket.gaierror, OSError):
            return None
        return None

    @staticmethod
    def _html_to_markdown(html_content: str) -> str:
        try:
            doc = Document(html_content)
            summary_html = doc.summary()
            return md(summary_html, strip=["img", "script", "style"])
        except Exception:
            return md(html_content, strip=["img", "script", "style"])

    async def _tool_web_fetch(self, url: str, max_length: int = 10000) -> str:
        _logger.debug("tool.web_fetch", url=url, max_length=max_length)

        # Validate scheme
        if not url.startswith(("http://", "https://")):
            return f"Error: URL must start with http:// or https://. Got: {url}"

        # SSRF protection — resolve hostname and check against private ranges
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return f"Error: Could not parse hostname from URL: {url}"

        resolved_ip = self._resolve_host(hostname)
        if resolved_ip is None:
            return f"Error: Could not resolve hostname: {hostname}"

        if self._is_private_ip(resolved_ip):
            return (
                f"Error: URL resolves to private/internal IP {resolved_ip}. "
                f"Access denied (SSRF protection)."
            )

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=5,
                timeout=httpx.Timeout(_WEB_FETCH_TIMEOUT),
            ) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "NahidaBot/0.1 (web_fetch tool)"},
                )
                response.raise_for_status()

                if len(response.content) > _WEB_FETCH_MAX_BODY:
                    return (
                        f"Error: Response body exceeds "
                        f"{_WEB_FETCH_MAX_BODY // 1024 // 1024}MB limit."
                    )

                content_type = response.headers.get("content-type", "")

                if "text/html" in content_type:
                    result = self._html_to_markdown(response.text)
                else:
                    result = response.text

                if len(result) > max_length:
                    result = result[:max_length] + "\n... (content truncated)"

                return result

        except httpx.HTTPStatusError as e:
            return f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
        except httpx.RequestError as e:
            return f"Request failed: {e}"
        except Exception as e:
            _logger.exception("tool.web_fetch.error", url=url)
            return f"Failed to fetch URL: {e}"

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
        try:
            return await self.api.workspace_read(path)
        except FileNotFoundError:
            return ""
        except Exception as exc:
            if exc.__class__.__name__ in {
                "WorkspacePathError",
                "WorkspaceNotFoundError",
            }:
                raise
            return ""

    async def _tool_memory_read(
        self,
        query: str = "",
        days: int = 3,
        max_length: int = 10000,
    ) -> str:
        _logger.debug("tool.memory_read", query=query, days=days)
        paths = [
            MEMORY_FILE,
            MEMORY_SUMMARY_FILE,
            *recent_daily_memory_paths(days=max(days, 0)),
        ]
        max_chars = min(max(max_length, 1), MAX_TOOL_READ_CHARS)
        blocks: list[str] = []
        for path in paths:
            raw = await self._read_workspace_text_or_empty(path)
            filtered = filter_memory_text(raw, query).strip()
            if not filtered:
                continue
            blocks.append(f"## {path}\n{filtered}")

        if not blocks:
            return "No matching workspace memory found."

        result = "\n\n".join(blocks)
        if len(result) > max_chars:
            result = result[:max_chars].rstrip() + "\n... (memory truncated)"
        return result

    async def _tool_memory_write(
        self,
        content: str,
        target: str = "daily",
        section: str = "Notes",
    ) -> str:
        _logger.debug("tool.memory_write", target=target, section=section)
        error = validate_memory_content(content)
        if error is not None:
            return error
        if target not in {"daily", "long_term", "both"}:
            return "Error: target must be one of: daily, long_term, both."

        written: list[str] = []
        if target in {"daily", "both"}:
            path = daily_memory_path()
            existing = await self._read_workspace_text_or_empty(path)
            await self.api.workspace_write(path, append_daily_memory(existing, content))
            written.append(path)

        if target in {"long_term", "both"}:
            existing = await self._read_workspace_text_or_empty(MEMORY_FILE)
            await self.api.workspace_write(
                MEMORY_FILE,
                append_long_term_memory(existing, content, section=section),
            )
            written.append(MEMORY_FILE)

        return "Memory written: " + ", ".join(written)

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
        self.api.register_tool(
            "agent_yield",
            "Wait for a subagent task result. Initial implementation aliases agent_wait.",
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
        handoff_summary: str = "",
        timeout_seconds: int | None = None,
        notify: str = "done_only",
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
                timeout_seconds=timeout_seconds,
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

    async def _tool_cron_create(
        self,
        prompt: str,
        mode: str,
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
            job = await scheduler.create_job(
                platform=ctx.platform,
                chat_id=ctx.chat_id,
                prompt=prompt,
                mode=mode,
                fire_at=fire_at if mode == "once" else None,
                interval_seconds=interval_seconds,
                cron_expression=cron_expression,
                max_runs=max_runs,
                workspace_id=ctx.workspace_id,
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
        lines.append(f"  Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        return "\n".join(lines)

    async def _tool_cron_list(self) -> str:
        ctx = current_session.get()
        if ctx is None:
            return "Error: No active session context."

        scheduler = self._get_scheduler()
        if scheduler is None:
            return "Error: Scheduler is not available."

        jobs = await scheduler.list_jobs(ctx.platform, ctx.chat_id)
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
            lines.append(f"  {j.job_id}: [{j.mode}] {schedule} — {preview}")
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
        if job.platform != ctx.platform or job.chat_id != ctx.chat_id:
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
        if job.platform != ctx.platform or job.chat_id != ctx.chat_id:
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
        if job.platform != ctx.platform or job.chat_id != ctx.chat_id:
            return f"Error: Job '{job_id}' does not belong to this chat."

        deleted = await scheduler.delete_job(job_id)
        if deleted:
            return f"Deleted task {job_id}."
        return f"Task {job_id} was already deleted."

    # ── Command Handlers ──────────────────────────────────

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

        new_id = await self.api.start_new_session(inbound.platform, inbound.chat_id)
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

        lines = [
            f"Session: {session_id}",
            f"Provider: {provider_id}",
            f"Model: {model}",
        ]
        return "\n".join(lines)

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

    async def _cmd_help(
        self, *, args: str, inbound: InboundMessage, session_id: str
    ) -> str:
        commands = self.api.list_commands()
        if not commands:
            return "No commands available."
        lines = ["Available commands:"]
        for cmd in sorted(commands, key=lambda c: c.name):
            aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
            desc = f" — {cmd.description}" if cmd.description else ""
            lines.append(f"  /{cmd.name}{aliases}{desc}")
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
        if not results:
            return "No memory found."
        lines = ["Memory results:"]
        for idx, item in enumerate(results, start=1):
            title = ""
            metadata = getattr(item, "metadata", None)
            if isinstance(metadata, dict):
                title_value = metadata.get("title")
                if isinstance(title_value, str) and title_value:
                    title = f"{title_value}: "
            key = getattr(item, "key", "")
            content = getattr(item, "content", "")
            lines.append(f"{idx}. [{key}] {title}{str(content)[:500]}")
        return "\n".join(lines)

    async def _tool_workspace_read(self, path: str) -> str:
        """Read a text file from the active workspace."""
        return await self.api.workspace_read(path)

    async def _tool_workspace_write(self, path: str, content: str) -> str:
        """Write a text file to the active workspace."""
        await self.api.workspace_write(path, content)
        return f"Written workspace file: {path}"
