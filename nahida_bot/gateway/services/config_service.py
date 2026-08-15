"""Config read/validate/save/backup service.

All config file I/O goes through this service so that backup, checksum,
and redaction logic is centralized.
"""

from __future__ import annotations

import hashlib
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from nahida_bot.core.config import Settings, find_config_yaml
from nahida_bot.core.config_secrets import (
    SENSITIVE_KEY_PATTERN,
    is_sensitive_path,
)
from nahida_bot.core.config_validation import (
    ValidationIssue,
    ValidationReport,
    validate_settings,
)
from nahida_bot.core.yaml_edit import (
    YamlEditError,
    document_to_text,
    parse_yaml_text,
)

logger = structlog.get_logger(__name__)

# Fields whose values should be redacted by default. Model-declared
# sensitive fields (core.config.SensitiveStr) take priority; this pattern is
# the fallback for untyped sections (channels, plugin config, extra keys).
_REDACT_PATTERNS = SENSITIVE_KEY_PATTERN

_REDACT_PLACEHOLDER = "***"

_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB

# Managed backups kept per config file before pruning kicks in.
_MAX_BACKUPS = 30


@dataclass(slots=True)
class ConfigContent:
    raw: str
    checksum: str
    path: str
    mtime: str


@dataclass(slots=True)
class ConfigValueEntry:
    path: str
    type_: str
    value: str


@dataclass(slots=True)
class ConfigSaveResult:
    saved: bool
    backup_path: str | None = None
    checksum: str = ""
    restart_required: bool = True
    validation: ValidationReport | None = None


@dataclass(slots=True)
class ConfigDocument:
    raw: str
    redacted_raw: str
    checksum: str
    path: str
    mtime: str
    data: dict[str, Any]
    redacted_data: dict[str, Any]
    redacted_paths: list[str]
    entries: list[ConfigValueEntry]


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


def read_config_document(config_path: str | None = None) -> ConfigDocument:
    """Read config as both raw YAML and structured redacted data."""
    cfg = read_current_config(config_path=config_path)
    data = _parse_yaml_mapping(cfg.raw)
    redacted_data, redacted_paths = redact_config_data(data)
    redacted_raw = config_data_to_yaml(redacted_data)
    return ConfigDocument(
        raw=cfg.raw,
        redacted_raw=redacted_raw,
        checksum=cfg.checksum,
        path=cfg.path,
        mtime=cfg.mtime,
        data=data,
        redacted_data=redacted_data,
        redacted_paths=redacted_paths,
        entries=flatten_config_values(redacted_data),
    )


def redact_config_data(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a redacted deep copy and the redacted field paths."""
    redacted = deepcopy(data)
    paths: list[str] = []
    _redact_any(redacted, "", paths)
    return redacted, paths


def config_data_to_yaml(data: dict[str, Any]) -> str:
    """Serialize structured config data for validation or preview."""
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def flatten_yaml_values(raw_yaml: str) -> list[ConfigValueEntry]:
    """Return flattened config values for display in the WebUI."""
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    entries: list[ConfigValueEntry] = []
    _flatten_value(data, "", entries)
    return entries


def flatten_config_values(data: dict[str, Any]) -> list[ConfigValueEntry]:
    """Return flattened config values from already parsed config data."""
    entries: list[ConfigValueEntry] = []
    _flatten_value(data, "", entries)
    return entries


def _redact_dict(d: dict[str, Any]) -> None:
    for key in list(d.keys()):
        if isinstance(d[key], dict):
            _redact_dict(d[key])
        elif isinstance(d[key], list):
            _redact_list(d[key])
        elif isinstance(d[key], str) and _REDACT_PATTERNS.search(key):
            d[key] = "***"


def _redact_list(values: list[Any]) -> None:
    for value in values:
        if isinstance(value, dict):
            _redact_dict(value)
        elif isinstance(value, list):
            _redact_list(value)


def _redact_any(value: Any, path: str, out: list[str]) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            child_path = f"{path}.{key}" if path else str(key)
            child = value[key]
            if isinstance(child, (dict, list)):
                _redact_any(child, child_path, out)
            elif isinstance(child, str) and is_sensitive_path(child_path, str(key)):
                value[key] = _REDACT_PLACEHOLDER
                out.append(child_path)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _redact_any(child, f"{path}[{index}]", out)


def _flatten_value(value: Any, path: str, out: list[ConfigValueEntry]) -> None:
    if isinstance(value, dict):
        if path and not value:
            out.append(ConfigValueEntry(path=path, type_="dict", value="{}"))
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _flatten_value(child, child_path, out)
        return

    if isinstance(value, list):
        if not value:
            out.append(ConfigValueEntry(path=path, type_="list", value="[]"))
            return
        for index, child in enumerate(value):
            _flatten_value(child, f"{path}[{index}]", out)
        return

    out.append(
        ConfigValueEntry(
            path=path,
            type_=_value_type(value),
            value=_format_config_value(value),
        )
    )


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _format_config_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return '""' if value == "" else value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    dumped = yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=True,
        sort_keys=False,
    ).strip()
    return dumped.removesuffix("\n...")


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


def _parse_yaml_mapping(raw_yaml: str) -> dict[str, Any]:
    data = yaml.safe_load(raw_yaml)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")
    return data


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


def validate_config_data(data: dict[str, Any]) -> ValidationReport:
    """Validate structured config data without writing to disk."""
    return validate_config_text(config_data_to_yaml(data))


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
    backup_path = _backup_and_write(path, content, backup_dir=backup_dir)
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


def save_config_patch_with_backup(
    changes: list[dict[str, Any]],
    *,
    expected_checksum: str,
    config_path: str | None = None,
    backup_dir: str | None = None,
) -> ConfigSaveResult:
    """Apply path-level config changes and save with checksum/backup protection."""
    path = _resolve_config_path(config_path)
    if not path or not path.exists():
        return ConfigSaveResult(saved=False, validation=ValidationReport())

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

    try:
        doc = parse_yaml_text(current_raw)
        _apply_patch_changes(doc, changes)
    except (TypeError, ValueError, YamlEditError) as exc:
        report = ValidationReport(issues=[ValidationIssue("error", "", str(exc))])
        return ConfigSaveResult(saved=False, validation=report)

    content = document_to_text(doc)
    report = validate_config_text(content)
    if report.errors > 0:
        return ConfigSaveResult(saved=False, validation=report)

    redacted_fields = _find_redacted_placeholders(content)
    if redacted_fields:
        report.issues.append(
            ValidationIssue(
                "error",
                redacted_fields[0],
                f"Save rejected: content contains redacted placeholder "
                f"'{_REDACT_PLACEHOLDER}' in sensitive field(s): "
                f"{', '.join(redacted_fields)}.",
            )
        )
        return ConfigSaveResult(saved=False, validation=report)

    backup_path = _backup_and_write(path, content, backup_dir=backup_dir)
    new_checksum = _sha256(content)
    logger.info(
        "config.patch_saved",
        path=str(path),
        backup=backup_path,
        new_checksum=new_checksum,
        change_count=len(changes),
    )
    return ConfigSaveResult(
        saved=True,
        backup_path=backup_path,
        checksum=new_checksum,
        restart_required=True,
        validation=report,
    )


def _backup_and_write(path: Path, content: str, *, backup_dir: str | None) -> str:
    if backup_dir:
        bdir = Path(backup_dir)
    else:
        bdir = path.parent / "config_backups"
    bdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_file = bdir / f"config.yaml.{timestamp}.bak"
    suffix = 1
    while backup_file.exists():
        # Two saves within the same second must not overwrite each other.
        backup_file = bdir / f"config.yaml.{timestamp}-{suffix}.bak"
        suffix += 1
    shutil.copy2(path, backup_file)
    _prune_backups(bdir)

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)
    return str(backup_file)


def _prune_backups(bdir: Path, keep: int = _MAX_BACKUPS) -> None:
    """Keep only the newest *keep* managed backups (timestamped names sort
    chronologically). Never raises — pruning must not fail a save."""
    try:
        backups = sorted(bdir.glob("config.yaml.*.bak"), reverse=True)
        for old in backups[keep:]:
            old.unlink(missing_ok=True)
    except OSError:
        logger.debug("config.backup_prune_failed", dir=str(bdir))


def restore_config_backup(
    backup_name: str,
    *,
    config_path: str | None = None,
    backup_dir: str | None = None,
    expected_checksum: str | None = None,
) -> ConfigSaveResult:
    """Restore a managed backup over the current config file.

    The backup content is validated through the same pipeline as a normal
    save; the current file is itself backed up before being replaced, so a
    restore is always reversible.
    """
    path = _resolve_config_path(config_path)
    if not path or not path.exists():
        return ConfigSaveResult(
            saved=False,
            validation=ValidationReport(
                issues=[ValidationIssue("error", "", f"Config file not found: {path}")]
            ),
        )

    if backup_dir:
        bdir = Path(backup_dir)
    else:
        bdir = path.parent / "config_backups"
    backup_file = bdir / backup_name
    if Path(backup_name).name != backup_name or not backup_file.is_file():
        return ConfigSaveResult(
            saved=False,
            validation=ValidationReport(
                issues=[
                    ValidationIssue("error", "", f"Backup not found: {backup_name}")
                ]
            ),
        )

    content = backup_file.read_text(encoding="utf-8")
    report = validate_config_text(content, config_yaml_path=str(path))
    if report.errors > 0:
        return ConfigSaveResult(saved=False, validation=report)

    if expected_checksum:
        current_raw = path.read_text(encoding="utf-8")
        if _sha256(current_raw) != expected_checksum:
            return ConfigSaveResult(
                saved=False,
                validation=ValidationReport(
                    issues=[
                        ValidationIssue(
                            "error",
                            "",
                            "Config was modified externally (checksum mismatch). "
                            "Re-read and retry.",
                        )
                    ]
                ),
            )

    backup_path = _backup_and_write(path, content, backup_dir=backup_dir)
    logger.info(
        "config.backup_restored",
        path=str(path),
        backup=str(backup_file),
        new_backup=backup_path,
    )
    return ConfigSaveResult(
        saved=True,
        backup_path=backup_path,
        checksum=_sha256(content),
        restart_required=True,
        validation=report,
    )


def _apply_patch_changes(data: dict[str, Any], changes: list[dict[str, Any]]) -> None:
    for change in changes:
        path = str(change.get("path") or "").strip()
        if not path:
            raise ValueError("Patch change is missing path")

        secret_action = change.get("secret_action")
        if secret_action == "keep":
            continue

        if change.get("remove"):
            _remove_path(data, path)
            continue

        value = "" if secret_action == "clear" else change.get("value")
        _set_path(data, path, value)


def _parse_config_path(path: str) -> list[str | int]:
    segments: list[str | int] = []
    token = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if token:
                segments.append(token)
                token = ""
            index += 1
            continue
        if char == "[":
            if token:
                segments.append(token)
                token = ""
            end = path.find("]", index)
            if end == -1:
                raise ValueError(f"Invalid config path: {path}")
            raw_index = path[index + 1 : end]
            if not raw_index.isdigit():
                raise ValueError(f"Invalid list index in config path: {path}")
            segments.append(int(raw_index))
            index = end + 1
            continue
        token += char
        index += 1
    if token:
        segments.append(token)
    if not segments:
        raise ValueError("Config path is empty")
    return segments


def _new_container(next_segment: str | int) -> Any:
    return [] if isinstance(next_segment, int) else {}


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    segments = _parse_config_path(path)
    current: Any = data
    for offset, segment in enumerate(segments[:-1]):
        next_segment = segments[offset + 1]
        if isinstance(segment, int):
            if not isinstance(current, list):
                raise ValueError(f"Expected list while setting {path}")
            while len(current) <= segment:
                current.append(_new_container(next_segment))
            if current[segment] is None:
                current[segment] = _new_container(next_segment)
            current = current[segment]
            continue

        if not isinstance(current, dict):
            raise ValueError(f"Expected mapping while setting {path}")
        if segment not in current or current[segment] is None:
            current[segment] = _new_container(next_segment)
        current = current[segment]

    final = segments[-1]
    if isinstance(final, int):
        if not isinstance(current, list):
            raise ValueError(f"Expected list while setting {path}")
        while len(current) <= final:
            current.append(None)
        current[final] = value
        return

    if not isinstance(current, dict):
        raise ValueError(f"Expected mapping while setting {path}")
    current[final] = value


def _remove_path(data: dict[str, Any], path: str) -> None:
    segments = _parse_config_path(path)
    current: Any = data
    for segment in segments[:-1]:
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                return
            current = current[segment]
            continue
        if not isinstance(current, dict) or segment not in current:
            return
        current = current[segment]

    final = segments[-1]
    if isinstance(final, int):
        if isinstance(current, list) and final < len(current):
            current.pop(final)
        return
    if isinstance(current, dict):
        current.pop(final, None)


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
    """Resolve the config YAML path via the single core discovery rule.

    Delegates to :func:`nahida_bot.core.config.find_config_yaml` so the
    Gateway edits exactly the file the CLI would load (explicit argument >
    ``NAHIDA_CONFIG`` env var > ``./config.yaml``).
    """
    resolved = find_config_yaml(config_path)
    return Path(resolved) if resolved else None
