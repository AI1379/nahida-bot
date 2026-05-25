"""Config read/validate/save/backup service.

All config file I/O goes through this service so that backup, checksum,
and redaction logic is centralized.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from nahida_bot.core.config import Settings
from nahida_bot.core.config_validation import (
    ValidationIssue,
    ValidationReport,
    validate_settings,
)

logger = structlog.get_logger(__name__)

# Fields whose values should be redacted by default.
_REDACT_PATTERNS = re.compile(
    r"(api_key|token|secret|password|private_key)", re.IGNORECASE
)

_REDACT_PLACEHOLDER = "***"

_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB


@dataclass(slots=True)
class ConfigContent:
    raw: str
    checksum: str
    path: str
    mtime: str


@dataclass(slots=True)
class ConfigSaveResult:
    saved: bool
    backup_path: str | None = None
    checksum: str = ""
    restart_required: bool = True
    validation: ValidationReport | None = None


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_yaml(raw_yaml: str) -> str:
    """Return a copy of raw YAML with sensitive values replaced by '***'."""
    data = yaml.safe_load(raw_yaml)
    if not isinstance(data, dict):
        return raw_yaml
    _redact_dict(data)
    return yaml.dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False
    )


def _redact_dict(d: dict[str, Any]) -> None:
    for key in list(d.keys()):
        if isinstance(d[key], dict):
            _redact_dict(d[key])
        elif isinstance(d[key], str) and _REDACT_PATTERNS.search(key):
            d[key] = "***"


def read_current_config(config_path: str | None = None) -> ConfigContent:
    """Read the current config YAML from disk.

    Args:
        config_path: Explicit path, or None to auto-detect.

    Returns:
        ConfigContent with raw text, checksum, path, and mtime.
    """
    path = _resolve_config_path(config_path)
    if not path or not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    stat = path.stat()
    raw = path.read_text(encoding="utf-8")
    return ConfigContent(
        raw=raw,
        checksum=_sha256(raw),
        path=str(path),
        mtime=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    )


def validate_config_text(
    raw_yaml: str,
    *,
    config_yaml_path: str | None = None,
) -> ValidationReport:
    """Parse and validate config YAML text.

    Returns a ValidationReport. Does not write to disk.
    """
    try:
        data = yaml.safe_load(raw_yaml)
        if not isinstance(data, dict):
            return ValidationReport(
                issues=[ValidationIssue("error", "", "Config must be a YAML mapping")]
            )
    except yaml.YAMLError as exc:
        return ValidationReport(
            issues=[ValidationIssue("error", "", f"YAML parse error: {exc}")]
        )

    try:
        settings = Settings(**data)
    except Exception as exc:
        return ValidationReport(
            issues=[ValidationIssue("error", "", f"Config validation: {exc}")]
        )

    return validate_settings(settings)


def save_config_with_backup(
    content: str,
    *,
    expected_checksum: str,
    config_path: str | None = None,
    backup_dir: str | None = None,
) -> ConfigSaveResult:
    """Save config YAML with atomic write and automatic backup.

    Args:
        content: New YAML content.
        expected_checksum: The checksum the caller saw before editing.
        config_path: Explicit path, or None to auto-detect.
        backup_dir: Directory for backups. Defaults to data/config_backups/.

    Returns:
        ConfigSaveResult indicating success or failure.
    """
    path = _resolve_config_path(config_path)
    if not path or not path.exists():
        return ConfigSaveResult(saved=False, validation=ValidationReport())

    # Verify no external modification
    current_raw = path.read_text(encoding="utf-8")
    current_checksum = _sha256(current_raw)
    if current_checksum != expected_checksum:
        report = ValidationReport(
            issues=[
                ValidationIssue(
                    "error",
                    "",
                    f"Config was modified externally (checksum mismatch). "
                    f"Expected {expected_checksum}, got {current_checksum}. "
                    f"Re-read and retry.",
                )
            ]
        )
        return ConfigSaveResult(saved=False, validation=report)

    # Validate before saving
    report = validate_config_text(content)
    if report.errors > 0:
        return ConfigSaveResult(saved=False, validation=report)

    # Reject saves that contain redacted placeholders in sensitive fields
    redacted_fields = _find_redacted_placeholders(content)
    if redacted_fields:
        report.issues.append(
            ValidationIssue(
                "error",
                redacted_fields[0],
                f"Save rejected: content contains redacted placeholder "
                f"'{_REDACT_PLACEHOLDER}' in sensitive field(s): "
                f"{', '.join(redacted_fields)}. Re-read the config to get "
                f"actual values, or leave sensitive fields unchanged.",
            )
        )
        return ConfigSaveResult(saved=False, validation=report)

    # Backup
    backup_path = None
    if backup_dir:
        bdir = Path(backup_dir)
    else:
        bdir = path.parent / "config_backups"
    bdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_file = bdir / f"config.yaml.{timestamp}.bak"
    shutil.copy2(path, backup_file)
    backup_path = str(backup_file)

    # Atomic write: write to temp, then rename
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)

    new_checksum = _sha256(content)

    logger.info(
        "config.saved",
        path=str(path),
        backup=backup_path,
        new_checksum=new_checksum,
    )

    return ConfigSaveResult(
        saved=True,
        backup_path=backup_path,
        checksum=new_checksum,
        restart_required=True,
        validation=report,
    )


def list_backups(
    config_path: str | None = None,
    backup_dir: str | None = None,
) -> list[dict[str, str]]:
    """List available config backups."""
    path = _resolve_config_path(config_path)
    if backup_dir:
        bdir = Path(backup_dir)
    elif path:
        bdir = path.parent / "config_backups"
    else:
        return []

    if not bdir.exists():
        return []

    backups: list[dict[str, str]] = []
    for f in sorted(bdir.glob("config.yaml.*.bak"), reverse=True):
        backups.append(
            {
                "name": f.name,
                "path": str(f),
                "size": str(f.stat().st_size),
                "mtime": datetime.fromtimestamp(f.stat().st_mtime, UTC).isoformat(),
            }
        )
    return backups


def _find_redacted_placeholders(yaml_text: str) -> list[str]:
    """Return a list of paths where a sensitive field has the redact placeholder value."""
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    matches: list[str] = []
    _walk_for_placeholder(data, "", matches)
    return matches


def _walk_for_placeholder(data: dict[str, Any], prefix: str, out: list[str]) -> None:
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            _walk_for_placeholder(value, path, out)
        elif isinstance(value, str) and value == _REDACT_PLACEHOLDER:
            if _REDACT_PATTERNS.search(key):
                out.append(path)


def _resolve_config_path(config_path: str | None = None) -> Path | None:
    """Resolve the config YAML path.

    Priority: explicit argument > CONFIG_YAML env var > ./config.yaml
    """
    import os

    if config_path:
        return Path(config_path)

    env_path = os.environ.get("CONFIG_YAML")
    if env_path:
        return Path(env_path)

    candidate = Path("config.yaml")
    if candidate.exists():
        return candidate

    return None
