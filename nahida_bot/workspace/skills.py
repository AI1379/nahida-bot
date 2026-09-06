"""Workspace-owned skill discovery, metadata and content loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(slots=True, frozen=True)
class SkillInfo:
    """Lightweight metadata for a workspace skill (frontmatter only, no body)."""

    name: str
    description: str
    file_path: Path  # absolute path to SKILL.md


class SkillCatalog:
    """Load and query workspace skills by name or scan for catalog listing.

    Skills use ``{workspace_root}/{directory}/<skill-name>/SKILL.md`` with
    optional YAML frontmatter (``name``, ``description`` fields).

    Static methods — no instance state needed.
    """

    _skill_directories: tuple[str, ...] = (".agents/skills", "skills")

    # ── public helpers ────────────────────────────────────

    @classmethod
    def scan_catalog(cls, workspace_root: Path) -> list[SkillInfo]:
        """Return lightweight (name, description, path) for every workspace skill."""
        seen: dict[str, SkillInfo] = {}
        for directory in cls._skill_directories:
            root = workspace_root / directory
            if not root.exists() or not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                raw = skill_file.read_text(encoding="utf-8").strip()
                if not raw:
                    continue
                meta, _body = cls._parse_frontmatter(raw)
                name = str(meta.get("name") or skill_file.parent.name).strip()
                description = str(meta.get("description", "")).strip()
                seen[name] = SkillInfo(
                    name=name,
                    description=description,
                    file_path=skill_file,
                )
        return [seen[name] for name in sorted(seen)]

    @classmethod
    def build_catalog_text(cls, workspace_root: Path) -> str | None:
        """Build a compact system message listing available skills by name+description."""
        skills = cls.scan_catalog(workspace_root)
        if not skills:
            return None

        lines: list[str] = [
            "Available workspace skills (invoke with /<name> or via the skill tool):"
        ]
        for skill in skills:
            desc = skill.description[:120] if skill.description else "(no description)"
            lines.append(f"- {skill.name}: {desc}")

        return "\n".join(lines)

    @classmethod
    def load_skill_content(cls, workspace_root: Path, skill_name: str) -> str | None:
        """Load the full formatted content for a skill by name, or ``None``."""
        catalog = cls.scan_catalog(workspace_root)
        info = next((s for s in catalog if s.name == skill_name.lower()), None)
        # Try case-sensitive fallback
        if info is None:
            info = next((s for s in catalog if s.name == skill_name), None)
        if info is None:
            return None

        raw = info.file_path.read_text(encoding="utf-8").strip()
        metadata, body = cls._parse_frontmatter(raw)
        name = str(metadata.get("name") or info.name).strip()
        description = str(metadata.get("description", "")).strip()

        parts = [f"# Skill: {name}"]
        if description:
            parts.append(f"Description: {description}")
        parts.append(body.strip())
        return "\n\n".join(part for part in parts if part)

    @classmethod
    def list_skill_names(cls, workspace_root: Path) -> set[str]:
        """Return all skill names for the workspace."""
        return {s.name for s in cls.scan_catalog(workspace_root)}

    # ── internal helpers ──────────────────────────────────

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
        """Split YAML frontmatter from a SKILL.md string.

        Returns ``(metadata, body)``.  If frontmatter is absent or malformed
        the metadata dict will be empty and the body is the whole raw text.
        """
        if not raw.startswith("---"):
            return {}, raw
        _, sep, rest = raw.partition("\n")
        if not sep:
            return {}, raw
        frontmatter, sep2, body = rest.partition("\n---")
        if not sep2:
            return {}, raw
        try:
            parsed = yaml.safe_load(frontmatter) or {}
        except yaml.YAMLError:
            return {}, raw
        if not isinstance(parsed, dict):
            return {}, raw
        return {str(key): value for key, value in parsed.items()}, body.lstrip()
