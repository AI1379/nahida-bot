"""Workspace lifecycle and metadata manager."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from nahida_bot.workspace.exceptions import (
    WorkspaceAlreadyExistsError,
    WorkspaceNotFoundError,
    WorkspaceValidationError,
)
from nahida_bot.workspace.models import WorkspaceMetadata
from nahida_bot.workspace.sandbox import WorkspaceSandbox


class WorkspaceManager:
    """Manage workspace creation, default workspace, and active selection."""

    default_instruction_files: tuple[str, ...] = ("AGENTS.md", "SOUL.md", "USER.md")
    default_instruction_content: dict[str, str] = {
        "AGENTS.md": """# Nahida Bot Workspace

This directory is the agent workspace. Treat files here as user-owned working
state and use workspace tools before assuming missing context.

## Startup Routine

1. Follow the system prompt first.
2. Read `SOUL.md` for persona and boundaries.
3. Read `USER.md` for user preferences and long-running context.
4. Use skills from `skills/*/SKILL.md` when a task matches their description.
5. Use `memory_read` / `memory_write` for durable workspace memory.
6. Keep replies concise and actionable unless the user asks for more detail.

## Workspace Rules

- Prefer `workspace_read` before editing a file you have not inspected.
- Prefer `workspace_write` for durable notes or generated artifacts.
- Use `memory_write` only for stable preferences, decisions, project facts, or
  explicit requests to remember something.
- Do not store secrets unless the user explicitly asks.
""",
        "SOUL.md": """# Nahida Bot Soul

You are Nahida Bot, a practical agent assistant.

## Tone

- Direct, calm, and technically precise.
- No empty encouragement or performative enthusiasm.
- Explain tradeoffs when they affect correctness, safety, or user time.

## Boundaries

- Ask only when the missing answer blocks safe progress.
- Preserve user-owned files and preferences.
- Treat workspace memory as helpful context, not unquestionable truth.
""",
        "USER.md": """# User Profile

Add durable preferences and personal context here.

## Preferences

- Language:
- Preferred response style:
- Important constraints:

## Long-Running Context

- Current projects:
- Things to remember:
""",
    }
    default_skill_files: dict[str, str] = {
        "skills/workspace-files/SKILL.md": """---
name: workspace-files
description: Read and write files in the active workspace.
---
# Workspace Files

Use this skill when the user asks you to inspect, create, update, or remember
workspace files.

## Available Tools

- `workspace_read(path)` reads a UTF-8 text file from the active workspace.
- `workspace_write(path, content)` writes UTF-8 text into the active workspace.

## Rules

- Use relative paths only.
- Read an existing file before changing it.
- Keep generated notes small and easy to scan.
- Do not write secrets unless the user explicitly asks.
""",
        "skills/memory/SKILL.md": """---
name: memory
description: Read and write durable workspace memory.
---
# Memory

Use this skill when the user asks you to remember something, when durable
workspace context would help, or when you need to check remembered preferences.

## Available Tools

- `memory_read(query?, max_length?)` searches structured durable memory and compatible workspace notes.
- `memory_write(content, title?, kind?, audience?, sensitivity?)` creates a structured memory item.
- `memory_update(item_id, content, ...)` replaces an outdated visible item and archives the old version.
- `memory_archive(item_id, reason)` archives an obsolete, wrong, or duplicate visible item.

## Rules

- Treat memory as helpful context, not unquestionable truth.
- Current user instructions and current files take precedence.
- Only write stable preferences, decisions, project facts, task outcomes, or
  explicit user requests to remember something.
- If there is no stable durable information, do not write a memory.
- Default audience to `current`; use `global` only for public knowledge that
  intentionally applies across every chat and user. Summaries are never global.
- Read an item before updating or archiving it. Prefer update over creating a
  contradictory duplicate.
- Do not write secrets, tokens, cookies, private keys, temporary URLs, base64,
  or raw event dumps.
""",
        "skills/tldr/SKILL.md": """---
name: tldr
description: Summarize recent conversation history when the user asks for a recap, TLDR, or catch-up.
---
# TLDR / Summarization

Use this skill when the user asks you to summarize what happened, catch them up,
give a TLDR, or recap recent conversation. Also use it when the user returns
after being away and asks "what did I miss?"

## Available Tools

- `read_chat_history(mode, limit?, before_turn_id?, since?, until?, chat_address?)`
  fetches raw conversation turns from the current or another chat.
  Supports `recent`, `time_range`, `around_message`, and `search` modes.
  Use `before_turn_id` to paginate backward through longer history.
- `search_chat_history(query, chat_address?)` searches across sessions by text.

## Workflow

### 1. Determine scope

Ask yourself:
- Did the user ask for a specific time range ("last hour", "since I left")?
- Did they ask for a specific number of messages ("last 100 messages")?
- Do they just want a general recap from when they were last active?

### 2. Fetch history

- For **recent messages**, use `read_chat_history(mode="recent", limit=N)`.
- For a **time range**, use `read_chat_history(mode="time_range", since="...", until="...")`.
- If you need **more turns than the tool returns in one call**, paginate with
  `before_turn_id` — the tool's output includes the cursor for the next page.
- For very long time spans, make multiple paginated calls until you have enough
  coverage, then synthesize.

### 3. Synthesize and present

Produce a concise, scannable summary. Structure it like this:

```
**Recap (since <time or reference point>):**

- <key topic or event 1>
- <key topic or event 2>
- ...

**<optional section headers for distinct topics>**
```

Rules for a good summary:
- **Be concise.** One sentence per major point. Skip filler and repetition.
- **Prioritize decisions, outcomes, and action items** over chatter.
- **Group related messages** into topic clusters rather than listing chronologically.
- **Mention who** if it matters (who asked, who decided), but stay brief.
- **If nothing significant happened**, say so directly instead of padding.
- **For group chats**, focus on topics the user cared about or was involved in.
  Don't narrate every casual message.
- For time-based requests, note the approximate timeframe covered.

### 4. Offer follow-up

After the summary, optionally ask if the user wants more detail on any specific
topic or a deeper look at a particular time window.
""",
    }
    default_memory_files: dict[str, str] = {
        "MEMORY.md": """# Memory

<!-- User-editable long-term workspace memory. Keep entries concise. -->

## Preferences

## Project Context

## Decisions
""",
    }

    def __init__(
        self, base_dir: Path, *, default_workspace_id: str = "default"
    ) -> None:
        """Initialize workspace manager.

        Args:
            base_dir: Directory that holds workspace folders and metadata index.
            default_workspace_id: Workspace ID used when bootstrapping a default workspace.
        """
        self.base_dir = base_dir.resolve(strict=False)
        self.workspaces_dir = self.base_dir / "workspaces"
        self.meta_file = self.base_dir / "workspace_index.json"
        self.active_file = self.base_dir / "active_workspace"
        self.default_workspace_id = self._validate_workspace_id(default_workspace_id)

    def initialize(self) -> WorkspaceMetadata:
        """Initialize storage and ensure the default workspace exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

        records = self._load_records()
        default_workspace_path = self.workspaces_dir / self.default_workspace_id
        if self.default_workspace_id not in records:
            records[self.default_workspace_id] = WorkspaceMetadata.create(
                self.default_workspace_id,
                is_default=True,
            )
            self._persist_records(records)
        default_workspace_path.mkdir(parents=True, exist_ok=True)
        self._ensure_default_instruction_files(default_workspace_path)
        self._ensure_default_skill_files(default_workspace_path)
        self._ensure_default_memory_files(default_workspace_path)

        default_metadata = records[self.default_workspace_id]
        default_metadata.is_default = True
        default_metadata.mark_active()
        records[self.default_workspace_id] = default_metadata
        self._persist_records(records)

        if not self.active_file.exists():
            self.active_file.write_text(self.default_workspace_id, encoding="utf-8")

        return default_metadata

    def create_workspace(
        self,
        workspace_id: str,
        *,
        template_dir: Path | None = None,
        make_active: bool = False,
    ) -> WorkspaceMetadata:
        """Create a workspace and optionally copy a template directory."""
        workspace_id = self._validate_workspace_id(workspace_id)
        records = self._load_records()
        if workspace_id in records:
            raise WorkspaceAlreadyExistsError(
                f"Workspace already exists: {workspace_id}"
            )

        workspace_path = self.workspace_path(workspace_id)
        workspace_path.mkdir(parents=True, exist_ok=False)

        try:
            if template_dir is not None:
                self._copy_template(
                    template_dir=template_dir, target_dir=workspace_path
                )
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise

        metadata = WorkspaceMetadata.create(workspace_id, is_default=False)
        records[workspace_id] = metadata
        self._persist_records(records)

        if make_active:
            return self.switch_workspace(workspace_id)

        return metadata

    def switch_workspace(self, workspace_id: str) -> WorkspaceMetadata:
        """Switch active workspace and refresh last active timestamp."""
        workspace_id = self._validate_workspace_id(workspace_id)
        records = self._load_records()
        if workspace_id not in records:
            raise WorkspaceNotFoundError(f"Workspace not found: {workspace_id}")

        metadata = records[workspace_id]
        metadata.mark_active()
        records[workspace_id] = metadata
        self._persist_records(records)
        self.active_file.write_text(workspace_id, encoding="utf-8")
        return metadata

    def list_workspaces(self) -> list[WorkspaceMetadata]:
        """Return all known workspaces sorted by ID."""
        records = self._load_records()
        return [records[key] for key in sorted(records)]

    def get_active_workspace(self) -> WorkspaceMetadata:
        """Return metadata for current active workspace."""
        if not self.active_file.exists():
            self.initialize()

        workspace_id = self.active_file.read_text(encoding="utf-8").strip()
        records = self._load_records()
        if workspace_id not in records:
            raise WorkspaceNotFoundError(
                f"Active workspace not found in index: {workspace_id}"
            )
        return records[workspace_id]

    def get_sandbox(self, workspace_id: str | None = None) -> WorkspaceSandbox:
        """Build a sandbox for given workspace or current active workspace."""
        selected_workspace = workspace_id
        if selected_workspace is None:
            selected_workspace = self.get_active_workspace().workspace_id

        records = self._load_records()
        if selected_workspace not in records:
            raise WorkspaceNotFoundError(f"Workspace not found: {selected_workspace}")
        return WorkspaceSandbox(self.workspace_path(selected_workspace))

    def workspace_path(self, workspace_id: str) -> Path:
        """Return root path for a workspace ID."""
        safe_id = self._validate_workspace_id(workspace_id)
        return self.workspaces_dir / safe_id

    def _validate_workspace_id(self, workspace_id: str) -> str:
        value = workspace_id.strip()
        if not value:
            raise WorkspaceValidationError("Workspace ID cannot be empty")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise WorkspaceValidationError(
                "Workspace ID must only contain letters, digits, '_' or '-'"
            )
        return value

    def _load_records(self) -> dict[str, WorkspaceMetadata]:
        if not self.meta_file.exists():
            return {}

        payload = json.loads(self.meta_file.read_text(encoding="utf-8"))
        items = payload.get("workspaces", {})
        return {
            key: WorkspaceMetadata.from_dict(value)
            for key, value in items.items()
            if isinstance(value, dict)
        }

    def _persist_records(self, records: dict[str, WorkspaceMetadata]) -> None:
        serializable = {
            workspace_id: metadata.to_dict()
            for workspace_id, metadata in records.items()
        }
        payload = {"workspaces": serializable}
        self.meta_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _copy_template(self, *, template_dir: Path, target_dir: Path) -> None:
        template = template_dir.resolve(strict=False)
        if not template.exists() or not template.is_dir():
            raise WorkspaceNotFoundError(
                f"Workspace template directory not found: {template_dir}"
            )

        for source_path in template.rglob("*"):
            relative = source_path.relative_to(template)
            destination = target_dir / relative
            if source_path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)

    def _ensure_default_instruction_files(self, workspace_path: Path) -> None:
        """Create editable instruction files for a new or existing workspace.

        Existing non-empty files are user-owned and are never overwritten.
        """
        for filename in self.default_instruction_files:
            path = workspace_path / filename
            if not path.exists() or not path.read_text(encoding="utf-8").strip():
                path.write_text(
                    self.default_instruction_content[filename],
                    encoding="utf-8",
                )

    def _ensure_default_skill_files(self, workspace_path: Path) -> None:
        """Create default workspace skills without overwriting user content."""
        for relative_path, content in self.default_skill_files.items():
            path = workspace_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or not path.read_text(encoding="utf-8").strip():
                path.write_text(content, encoding="utf-8")

    def _ensure_default_memory_files(self, workspace_path: Path) -> None:
        """Create editable memory files without overwriting user content."""
        for relative_path, content in self.default_memory_files.items():
            path = workspace_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or not path.read_text(encoding="utf-8").strip():
                path.write_text(content, encoding="utf-8")
