"""Managed temporary files for plugin-produced outbound attachments."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import structlog

from nahida_bot.plugins.base import Attachment, OutboundMessage
from nahida_bot_sdk.api import ManagedTempFile

logger = structlog.get_logger(__name__)

_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(slots=True, frozen=True)
class _TempFileMeta:
    token: str
    plugin_id: str
    path: str
    purpose: str
    created_at: float
    expires_at: float


class ManagedTempFileService:
    """Allocate and garbage-collect plugin-scoped temporary files."""

    def __init__(self, root: Path, *, default_ttl_seconds: int = 3600) -> None:
        self._root = root.expanduser().resolve(strict=False)
        self._default_ttl_seconds = max(1, default_ttl_seconds)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    async def create_temp_file(
        self,
        *,
        plugin_id: str,
        suffix: str = "",
        prefix: str = "",
        purpose: str = "",
        ttl_seconds: int = 3600,
    ) -> ManagedTempFile:
        """Allocate an empty managed temporary file for a plugin."""
        ttl = max(1, int(ttl_seconds or self._default_ttl_seconds))
        token = uuid4().hex
        safe_plugin_id = _safe_path_part(plugin_id) or "plugin"
        safe_prefix = _safe_path_part(prefix)[:40]
        safe_suffix = _safe_suffix(suffix)
        file_name = f"{safe_prefix + '-' if safe_prefix else ''}{token}{safe_suffix}"
        plugin_dir = self._root / safe_plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)
        path = plugin_dir / file_name
        path.touch(exist_ok=False)

        now = time.time()
        meta = _TempFileMeta(
            token=token,
            plugin_id=plugin_id,
            path=str(path),
            purpose=purpose,
            created_at=now,
            expires_at=now + ttl,
        )
        self._meta_path(path).write_text(
            json.dumps(asdict(meta), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        logger.debug(
            "managed_temp_file.created",
            plugin_id=plugin_id,
            path=str(path),
            purpose=purpose,
            ttl_seconds=ttl,
        )
        return ManagedTempFile(
            path=str(path),
            plugin_id=plugin_id,
            cleanup_token=token,
            ttl_seconds=ttl,
        )

    async def cleanup_message(self, message: OutboundMessage) -> int:
        """Clean managed temp attachments marked for cleanup after send."""
        removed = 0
        for attachment in message.attachments:
            if await self.cleanup_attachment(attachment):
                removed += 1
        return removed

    async def cleanup_attachment(
        self, attachment: Attachment, *, ignore_cleanup_after_send: bool = False
    ) -> bool:
        """Clean a managed temp attachment after successful send."""
        extra = attachment.extra
        if not extra.get("managed_temp_file"):
            return False
        if extra.get("cleanup_after_send") is False and not ignore_cleanup_after_send:
            return False
        token = str(extra.get("cleanup_token") or "")
        if not token:
            return False
        return self._cleanup_path(Path(attachment.path), token=token)

    async def cleanup_expired(self) -> int:
        """Delete all expired managed temp files."""
        now = time.time()
        removed = 0
        for meta_path in self._root.rglob("*.meta.json"):
            meta = self._read_meta(meta_path)
            if meta is None:
                _unlink_quietly(meta_path)
                continue
            if meta.expires_at <= now and self._cleanup_path(Path(meta.path)):
                removed += 1
        return removed

    async def cleanup_plugin(self, plugin_id: str, *, expired_only: bool = True) -> int:
        """Delete managed temp files belonging to one plugin."""
        safe_plugin_id = _safe_path_part(plugin_id) or "plugin"
        plugin_dir = self._root / safe_plugin_id
        if not plugin_dir.exists():
            return 0
        now = time.time()
        removed = 0
        for meta_path in plugin_dir.rglob("*.meta.json"):
            meta = self._read_meta(meta_path)
            if meta is None:
                _unlink_quietly(meta_path)
                continue
            if expired_only and meta.expires_at > now:
                continue
            if self._cleanup_path(Path(meta.path)):
                removed += 1
        return removed

    def _cleanup_path(self, path: Path, *, token: str = "") -> bool:
        path = path.expanduser().resolve(strict=False)
        if not _is_relative_to(path, self._root):
            logger.warning(
                "managed_temp_file.cleanup_rejected",
                path=str(path),
                reason="outside_root",
            )
            return False

        meta_path = self._meta_path(path)
        meta = self._read_meta(meta_path)
        if meta is None:
            return False
        if token and meta.token != token:
            logger.warning(
                "managed_temp_file.cleanup_rejected",
                path=str(path),
                reason="token_mismatch",
            )
            return False
        if Path(meta.path).expanduser().resolve(strict=False) != path:
            logger.warning(
                "managed_temp_file.cleanup_rejected",
                path=str(path),
                reason="metadata_path_mismatch",
            )
            return False

        removed_file = False
        if path.is_file() or path.is_symlink():
            removed_file = _unlink_quietly(path)
        _unlink_quietly(meta_path)
        logger.debug(
            "managed_temp_file.cleaned",
            plugin_id=meta.plugin_id,
            path=str(path),
            purpose=meta.purpose,
            removed_file=removed_file,
        )
        return True

    @staticmethod
    def _meta_path(path: Path) -> Path:
        return path.with_name(f"{path.name}.meta.json")

    def _read_meta(self, meta_path: Path) -> _TempFileMeta | None:
        meta_path = meta_path.expanduser().resolve(strict=False)
        if not _is_relative_to(meta_path, self._root):
            return None
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            return _TempFileMeta(
                token=str(raw.get("token") or ""),
                plugin_id=str(raw.get("plugin_id") or ""),
                path=str(raw.get("path") or ""),
                purpose=str(raw.get("purpose") or ""),
                created_at=float(raw.get("created_at") or 0),
                expires_at=float(raw.get("expires_at") or 0),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None


def _safe_path_part(value: str) -> str:
    value = _SAFE_PART_RE.sub("-", value.strip())
    return value.strip(".-")[:80]


def _safe_suffix(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not value.startswith("."):
        value = f".{value}"
    value = _SAFE_PART_RE.sub("-", value)
    value = "." + value.lstrip(".-")
    return value[:32]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _unlink_quietly(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
