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

from nahida_bot.plugins.base import InboundMessage, Plugin

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
        self._register_exec_tool()
        self._register_web_fetch_tool()
        self._register_plan_tool()

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
            lines = ["Available models:"]
            for entry in models:
                marker = " (current)" if entry["model"] == current_model else ""
                lines.append(f"  {entry['provider_id']}/{entry['model']}{marker}")
            return "\n".join(lines)

        model_name = args.strip()
        provider_id = await self.api.set_session_model(session_id, model_name)
        if provider_id is not None:
            return f"Switched to {model_name} (via {provider_id})"
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

    async def _tool_workspace_read(self, path: str) -> str:
        """Read a text file from the active workspace."""
        return await self.api.workspace_read(path)

    async def _tool_workspace_write(self, path: str, content: str) -> str:
        """Write a text file to the active workspace."""
        await self.api.workspace_write(path, content)
        return f"Written workspace file: {path}"
