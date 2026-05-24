"""Inspect and apply legacy session-key migrations.

This script is intentionally conservative. ``inspect`` is read-only and builds
a JSON plan from evidence stored in session and turn metadata. ``apply`` only
executes explicitly approved entries and does not auto-apply split plans.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal, cast

try:
    from nahida_bot.core.chat_address import (
        ChatAddress,
        SessionKey,
        classify_session_key,
        is_valid_target_type,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"nahida_bot", "structlog"}:
        raise
    _CHAT_ADDRESS_PATH = (
        Path(__file__).resolve().parents[1] / "nahida_bot" / "core" / "chat_address.py"
    )
    _SPEC = importlib.util.spec_from_file_location(
        "_nahida_migration_chat_address", _CHAT_ADDRESS_PATH
    )
    if _SPEC is None or _SPEC.loader is None:
        raise
    _chat_address = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = _chat_address
    _SPEC.loader.exec_module(_chat_address)
    ChatAddress = _chat_address.ChatAddress
    SessionKey = _chat_address.SessionKey
    classify_session_key = _chat_address.classify_session_key
    is_valid_target_type = _chat_address.is_valid_target_type

PLAN_VERSION = 1

Recommendation = Literal[
    "rename",
    "split",
    "keep_legacy",
    "disable_cron",
    "skip_typed",
    "manual_review",
]
Confidence = Literal["high", "medium", "low", "conflict"]
Approval = Literal[
    "pending",
    "approved",
    "rejected",
    "force_keep_legacy",
    "force_rename",
    "force_split",
]
CronHistoryMode = Literal["none", "active", "all"]


@dataclass(slots=True)
class Evidence:
    source: str
    chat_address: str
    turn_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AffectedRows:
    sessions: int = 0
    memory_turns: int = 0
    active_sessions: int = 0
    cron_jobs: int = 0
    background_tasks: int = 0
    memory_items: int = 0
    memory_item_fts: int = 0
    memory_candidates: int = 0


@dataclass(slots=True)
class SplitTarget:
    chat_address: str
    new_session_id: str
    turn_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class PlanEntry:
    old_session_id: str
    status: str
    recommendation: Recommendation
    new_session_id: str | None
    confidence: Confidence
    evidence: list[Evidence]
    affected: AffectedRows
    approval: Approval = "pending"
    notes: str = ""
    split_targets: list[SplitTarget] = field(default_factory=list)


@dataclass(slots=True)
class MigrationPlan:
    version: int
    generated_at: str
    db_path: str
    summary: dict[str, int]
    entries: list[PlanEntry]


def inspect_database(db_path: Path) -> MigrationPlan:
    """Build a read-only migration plan for the database."""
    conn = _connect(db_path, read_only=True)
    try:
        entries: list[PlanEntry] = []
        for row in conn.execute(
            """
            SELECT session_id, workspace_id, created_at, last_active_at, metadata_json
            FROM sessions
            ORDER BY session_id
            """
        ):
            session_id = str(row["session_id"])
            kind = classify_session_key(session_id)
            if kind.startswith("typed"):
                entries.append(_skip_typed_entry(conn, session_id))
                continue
            if kind == "invalid":
                entries.append(_invalid_session_entry(conn, session_id))
                continue
            entries.append(_inspect_legacy_session(conn, row))

        summary = _summarize_entries(entries)
        return MigrationPlan(
            version=PLAN_VERSION,
            generated_at=_utc_now(),
            db_path=str(db_path),
            summary=summary,
            entries=entries,
        )
    finally:
        conn.close()


def apply_plan(
    *,
    db_path: Path,
    plan_path: Path,
    dry_run: bool = False,
    backup: bool = True,
    stop_on_error: bool = False,
) -> dict[str, int]:
    """Apply approved migration plan entries."""
    plan = _load_plan(plan_path)
    if not dry_run and backup and str(db_path) != ":memory:":
        backup_path = _backup_database(db_path)
        print(f"Backup written: {backup_path}")

    conn = _connect(db_path, read_only=False)
    try:
        _ensure_migration_log(conn)
        conn.commit()
        results = Counter[str]()
        for entry in plan.entries:
            if entry.approval == "pending":
                results["pending"] += 1
                continue
            if _entry_already_applied(conn, entry):
                results["already_applied"] += 1
                continue
            try:
                if not dry_run:
                    conn.execute("BEGIN")
                action = _apply_entry(conn, entry, dry_run=dry_run)
                if dry_run:
                    conn.rollback()
                else:
                    conn.commit()
                results[action] += 1
            except Exception as exc:  # noqa: BLE001 - script should continue
                conn.rollback()
                results["failed"] += 1
                print(f"FAILED {entry.old_session_id}: {type(exc).__name__}: {exc}")
                if stop_on_error:
                    raise
        return dict(results)
    finally:
        conn.close()


def repair_cron_sessions(
    *,
    db_path: Path,
    dry_run: bool = False,
    backup: bool = True,
    migrate_history: CronHistoryMode = "none",
) -> dict[str, int]:
    """Repair cron chat_type/session_key rows after session-key migration.

    This is intentionally separate from plan/apply. It handles post-migration
    cron rows whose base session_key is typed but chat_type was left empty,
    and can optionally move legacy isolated cron history to the typed runtime
    session id.
    """
    if migrate_history not in {"none", "active", "all"}:
        raise ValueError("migrate_history must be one of: none, active, all")
    if not dry_run and backup and str(db_path) != ":memory:":
        backup_path = _backup_database(db_path)
        print(f"Backup written: {backup_path}")

    conn = _connect(db_path, read_only=False)
    try:
        results = Counter[str]()
        if not _table_exists(conn, "cron_jobs"):
            return {"cron_table_missing": 1}

        if not dry_run:
            conn.execute("BEGIN")
        _ensure_migration_log(conn)
        has_chat_type = _column_exists(conn, "cron_jobs", "chat_type")
        for row in _iter_cron_repair_rows(conn):
            address = _cron_row_address(row)
            if address is None:
                results["cron_untyped"] += 1
                continue

            current_session_key = str(row["session_key"])
            if current_session_key != address.chat_key:
                _repair_cron_session_key(conn, row, address, has_chat_type)
                results["session_key_repaired"] += 1
            elif has_chat_type:
                existing_chat_type = str(row["chat_type"] or "")
                if not existing_chat_type:
                    conn.execute(
                        "UPDATE cron_jobs SET chat_type = ? WHERE job_id = ?",
                        (address.target_type, row["job_id"]),
                    )
                    results["chat_type_filled"] += 1
                elif existing_chat_type != address.target_type:
                    results["chat_type_conflict"] += 1

            if str(row["session_mode"] or "main") != "isolated":
                continue
            history_action = _repair_isolated_cron_history(
                conn,
                row=row,
                address=address,
                migrate_history=migrate_history,
                dry_run=dry_run,
            )
            results[history_action] += 1

        if dry_run:
            conn.rollback()
        else:
            _assert_no_foreign_key_errors(conn, dry_run=False)
            conn.commit()
        return dict(results)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def write_plan(plan: MigrationPlan, out_path: Path) -> None:
    out_path.write_text(
        json.dumps(_plan_to_dict(plan), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def review_plan_interactively(
    plan: MigrationPlan,
    *,
    prompt_fn: Callable[[str], str] = input,
) -> dict[str, int]:
    """Prompt for approvals and mutate plan entries in place."""
    result = Counter[str]()
    for entry in plan.entries:
        if entry.approval != "pending":
            continue
        if entry.recommendation == "skip_typed":
            continue
        if entry.recommendation == "manual_review":
            result["manual_review"] += 1
            continue

        prompt = _approval_prompt_for_entry(entry)
        if _prompt_yes_no(prompt, prompt_fn=prompt_fn):
            entry.approval = (
                "force_split" if entry.recommendation == "split" else "approved"
            )
            entry.status = "approved"
            result["approved"] += 1
        else:
            result["pending"] += 1
    return dict(result)


def print_summary(summary: dict[str, int]) -> None:
    print(f"Sessions scanned: {summary.get('total', 0)}")
    print(f"High confidence rename: {summary.get('rename_high', 0)}")
    print(f"Needs manual review: {summary.get('manual_review', 0)}")
    print(f"Split suggested: {summary.get('split', 0)}")
    print(f"Keep legacy suggested: {summary.get('keep_legacy', 0)}")
    print(f"Typed already: {summary.get('skip_typed', 0)}")
    print(f"Cron jobs to disable unless approved: {summary.get('disable_cron', 0)}")


def print_apply_summary(summary: dict[str, int]) -> None:
    labels = {
        "renamed": "Renamed",
        "split": "Split",
        "kept_legacy": "Kept legacy",
        "cron_disabled": "Cron disabled",
        "rejected": "Rejected",
        "already_applied": "Already applied",
        "pending": "Pending skipped",
        "failed": "Failed",
    }
    total = sum(summary.values())
    print(f"Plan entries considered: {total}")
    for key, label in labels.items():
        print(f"{label}: {summary.get(key, 0)}")


def print_cron_repair_summary(summary: dict[str, int]) -> None:
    labels = {
        "chat_type_filled": "Chat type filled",
        "session_key_repaired": "Session key repaired",
        "chat_type_conflict": "Chat type conflicts",
        "cron_untyped": "Cron rows without typed evidence",
        "isolated_history_migrated": "Isolated history migrated",
        "isolated_history_left_legacy": "Isolated history left legacy",
        "isolated_history_already_typed": "Isolated history already typed",
        "isolated_history_missing": "Isolated history missing",
        "cron_table_missing": "Cron table missing",
    }
    total = sum(summary.values())
    print(f"Cron rows/actions considered: {total}")
    for key, label in labels.items():
        print(f"{label}: {summary.get(key, 0)}")


def _inspect_legacy_session(conn: sqlite3.Connection, row: sqlite3.Row) -> PlanEntry:
    session_id = str(row["session_id"])
    evidence_by_address, turn_ids_by_address = _collect_evidence(conn, row)
    affected = _affected_rows(conn, session_id)

    if not evidence_by_address:
        if affected.cron_jobs and _can_disable_cron_for_session(session_id):
            return PlanEntry(
                old_session_id=session_id,
                status="needs_approval",
                recommendation="disable_cron",
                new_session_id=None,
                confidence="low",
                evidence=[],
                affected=affected,
                notes=(
                    "No typed chat-address evidence found; approve to disable "
                    "legacy cron jobs and keep the session marked legacy."
                ),
            )
        return PlanEntry(
            old_session_id=session_id,
            status="needs_approval",
            recommendation="keep_legacy",
            new_session_id=None,
            confidence="low",
            evidence=[],
            affected=affected,
            notes="No typed chat-address evidence found.",
        )

    evidence = _evidence_list(evidence_by_address)
    if len(evidence_by_address) == 1:
        address_text = next(iter(evidence_by_address))
        address = ChatAddress.parse(address_text)
        old_key = SessionKey.parse(session_id)
        new_session_id = str(SessionKey(address=address, suffix=old_key.suffix))
        confidence: Confidence = "high" if affected.memory_turns else "medium"
        return PlanEntry(
            old_session_id=session_id,
            status="needs_approval",
            recommendation="rename",
            new_session_id=new_session_id,
            confidence=confidence,
            evidence=evidence,
            affected=affected,
            notes="All typed evidence points to one chat address.",
        )

    split_targets = [
        SplitTarget(
            chat_address=address_text,
            new_session_id=str(
                SessionKey(
                    address=ChatAddress.parse(address_text),
                    suffix=SessionKey.parse(session_id).suffix,
                )
            ),
            turn_ids=sorted(turn_ids_by_address.get(address_text, [])),
        )
        for address_text in sorted(evidence_by_address)
    ]
    return PlanEntry(
        old_session_id=session_id,
        status="needs_approval",
        recommendation="split",
        new_session_id=None,
        confidence="conflict",
        evidence=evidence,
        affected=affected,
        notes="Multiple typed chat addresses found; split requires manual approval.",
        split_targets=split_targets,
    )


def _approval_prompt_for_entry(entry: PlanEntry) -> str:
    if entry.recommendation == "rename":
        return (
            f"Approve rename {entry.old_session_id} -> {entry.new_session_id}? [y/N]: "
        )
    if entry.recommendation == "split":
        target_count = len(entry.split_targets)
        return (
            f"Approve split {entry.old_session_id} into {target_count} target "
            f"session(s)? [y/N]: "
        )
    if entry.recommendation == "keep_legacy":
        return f"Approve keeping {entry.old_session_id} as legacy? [y/N]: "
    if entry.recommendation == "disable_cron":
        cron_count = entry.affected.cron_jobs
        label = "job" if cron_count == 1 else "jobs"
        return (
            f"Approve disabling {cron_count} cron {label} for "
            f"{entry.old_session_id}? [y/N]: "
        )
    return f"Approve {entry.old_session_id}? [y/N]: "


def _prompt_yes_no(prompt: str, *, prompt_fn: Callable[[str], str]) -> bool:
    while True:
        answer = prompt_fn(prompt).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("Please answer y or n.")


def _skip_typed_entry(conn: sqlite3.Connection, session_id: str) -> PlanEntry:
    return PlanEntry(
        old_session_id=session_id,
        status="skipped",
        recommendation="skip_typed",
        new_session_id=None,
        confidence="high",
        evidence=[],
        affected=_affected_rows(conn, session_id),
        notes="Session already uses a typed key.",
    )


def _invalid_session_entry(conn: sqlite3.Connection, session_id: str) -> PlanEntry:
    return PlanEntry(
        old_session_id=session_id,
        status="needs_approval",
        recommendation="manual_review",
        new_session_id=None,
        confidence="low",
        evidence=[],
        affected=_affected_rows(conn, session_id),
        notes="Session id cannot be parsed as a session key.",
    )


def _collect_evidence(
    conn: sqlite3.Connection, session_row: sqlite3.Row
) -> tuple[dict[str, Counter[str]], dict[str, list[int]]]:
    session_id = str(session_row["session_id"])
    evidence: dict[str, Counter[str]] = defaultdict(Counter)
    turn_ids_by_address: dict[str, list[int]] = defaultdict(list)

    session_meta = _json_dict(session_row["metadata_json"])
    _add_metadata_evidence(
        evidence,
        "sessions.metadata_json.chat_address",
        session_meta.get("chat_address"),
    )
    _add_metadata_evidence(
        evidence,
        "sessions.metadata_json.message_context",
        session_meta.get("message_context"),
    )

    for turn in conn.execute(
        """
        SELECT id, metadata_json
        FROM memory_turns
        WHERE session_id = ?
        ORDER BY id
        """,
        (session_id,),
    ):
        metadata = _json_dict(turn["metadata_json"])
        address = _address_from_message_context(metadata.get("message_context"))
        if address is None:
            continue
        address_text = str(address)
        evidence[address_text]["memory_turns.metadata_json.message_context"] += 1
        turn_ids_by_address[address_text].append(int(turn["id"]))

    if _table_exists(conn, "active_sessions"):
        for active in conn.execute(
            """
            SELECT chat_key, session_id
            FROM active_sessions
            WHERE chat_key = ? OR session_id = ? OR session_id LIKE ?
            """,
            (session_id, session_id, f"{session_id}:%"),
        ):
            _add_session_key_evidence(
                evidence,
                "active_sessions.chat_key",
                str(active["chat_key"]),
            )
            _add_session_key_evidence(
                evidence,
                "active_sessions.session_id",
                str(active["session_id"]),
            )

    if _table_exists(conn, "cron_jobs"):
        for cron in _cron_rows_for_session(conn, session_id):
            _add_session_key_evidence(
                evidence,
                "cron_jobs.session_key",
                str(cron["session_key"]),
            )
            _add_metadata_evidence(
                evidence,
                "cron_jobs.chat_type",
                {
                    "channel": cron["platform"],
                    "chat_type": cron["chat_type"],
                    "chat_id": cron["chat_id"],
                },
            )

    return dict(evidence), dict(turn_ids_by_address)


def _add_metadata_evidence(
    evidence: dict[str, Counter[str]], source: str, raw: object
) -> None:
    address = _address_from_chat_address(raw) or _address_from_message_context(raw)
    if address is not None:
        evidence[str(address)][source] += 1


def _add_session_key_evidence(
    evidence: dict[str, Counter[str]], source: str, value: str
) -> None:
    try:
        key = SessionKey.parse(value)
    except ValueError:
        return
    if key.address.is_typed:
        evidence[str(key.address)][source] += 1


def _address_from_chat_address(raw: object) -> ChatAddress | None:
    if not isinstance(raw, dict):
        return None
    channel = str(raw.get("channel") or "").strip()
    target_type = str(raw.get("target_type") or "").strip()
    target_id = str(raw.get("target_id") or "").strip()
    thread_id = str(raw.get("thread_id") or "").strip()
    if not channel or not target_id or not is_valid_target_type(target_type):
        return None
    address = ChatAddress(
        channel=channel,
        target_type=target_type,
        target_id=target_id,
        thread_id=thread_id,
    )
    return address if address.is_typed else None


def _address_from_message_context(raw: object) -> ChatAddress | None:
    if not isinstance(raw, dict):
        return None
    channel = str(raw.get("channel") or "").strip()
    chat_type = str(raw.get("chat_type") or "").strip()
    chat_id = str(raw.get("chat_id") or "").strip()
    if not channel or not chat_id or not is_valid_target_type(chat_type):
        return None
    address = ChatAddress(channel=channel, target_type=chat_type, target_id=chat_id)
    return address if address.is_typed else None


def _evidence_list(evidence_by_address: dict[str, Counter[str]]) -> list[Evidence]:
    result: list[Evidence] = []
    for address_text, sources in sorted(evidence_by_address.items()):
        for source, count in sorted(sources.items()):
            result.append(
                Evidence(
                    source=source,
                    chat_address=address_text,
                    turn_count=count if source.startswith("memory_turns.") else 0,
                    details={}
                    if source.startswith("memory_turns.")
                    else {"count": count},
                )
            )
    return result


def _can_disable_cron_for_session(session_id: str) -> bool:
    """Only base legacy chat sessions should disable ambiguous cron jobs."""
    key = _parse_session_key_or_none(session_id)
    return key is not None and not key.is_derived


def _cron_rows_for_session(
    conn: sqlite3.Connection, session_id: str
) -> list[sqlite3.Row]:
    if not _table_exists(conn, "cron_jobs"):
        return []
    chat_type_expr = (
        "chat_type" if _column_exists(conn, "cron_jobs", "chat_type") else "''"
    )
    cron_job_id = _cron_job_id_from_session_id(session_id)
    if cron_job_id is not None:
        return conn.execute(
            f"""
            SELECT platform, chat_id, session_key, {chat_type_expr} AS chat_type
            FROM cron_jobs
            WHERE job_id = ?
            """,
            (cron_job_id,),
        ).fetchall()

    key = _parse_session_key_or_none(session_id)
    if key is not None and key.is_derived:
        return conn.execute(
            f"""
            SELECT platform, chat_id, session_key, {chat_type_expr} AS chat_type
            FROM cron_jobs
            WHERE session_key = ?
            """,
            (session_id,),
        ).fetchall()

    old_chat_key = _legacy_reference_keys(session_id)[-1]
    return conn.execute(
        f"""
        SELECT platform, chat_id, session_key, {chat_type_expr} AS chat_type
        FROM cron_jobs
        WHERE session_key = ? OR session_key = ?
        """,
        (session_id, old_chat_key),
    ).fetchall()


def _iter_cron_repair_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    chat_type_expr = (
        "chat_type" if _column_exists(conn, "cron_jobs", "chat_type") else "''"
    )
    session_mode_expr = (
        "session_mode"
        if _column_exists(conn, "cron_jobs", "session_mode")
        else "'main'"
    )
    return conn.execute(
        f"""
        SELECT
            job_id, platform, chat_id, session_key, is_active,
            {session_mode_expr} AS session_mode,
            {chat_type_expr} AS chat_type
        FROM cron_jobs
        ORDER BY job_id
        """
    ).fetchall()


def _cron_row_address(row: sqlite3.Row) -> ChatAddress | None:
    key = _parse_session_key_or_none(str(row["session_key"]))
    if key is not None and key.address.is_typed:
        return key.address

    chat_type = str(row["chat_type"] or "").strip()
    if not chat_type or chat_type == "unknown":
        return None
    if not is_valid_target_type(chat_type):
        return None
    address = ChatAddress(
        channel=str(row["platform"]),
        target_type=chat_type,
        target_id=str(row["chat_id"]),
    )
    return address if address.is_typed else None


def _repair_cron_session_key(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    address: ChatAddress,
    has_chat_type: bool,
) -> None:
    if has_chat_type:
        conn.execute(
            """
            UPDATE cron_jobs
            SET session_key = ?, chat_type = ?
            WHERE job_id = ?
            """,
            (address.chat_key, address.target_type, row["job_id"]),
        )
        return
    conn.execute(
        "UPDATE cron_jobs SET session_key = ? WHERE job_id = ?",
        (address.chat_key, row["job_id"]),
    )


def _repair_isolated_cron_history(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    address: ChatAddress,
    migrate_history: CronHistoryMode,
    dry_run: bool,
) -> str:
    expected_session_id = f"{address.chat_key}:cron:{row['job_id']}"
    old_session_id = _existing_legacy_cron_session_id(conn, row, address)
    expected_exists = (
        _count(conn, "sessions", "session_id = ?", (expected_session_id,)) > 0
    )

    if old_session_id is None:
        return (
            "isolated_history_already_typed"
            if expected_exists
            else "isolated_history_missing"
        )
    if migrate_history == "none":
        return "isolated_history_left_legacy"
    if migrate_history == "active" and not bool(row["is_active"]):
        return "isolated_history_left_legacy"

    _rename_session(
        conn,
        old_session_id=old_session_id,
        new_session_id=expected_session_id,
        dry_run=dry_run,
    )
    _record_log(
        conn,
        old_session_id=old_session_id,
        new_session_id=expected_session_id,
        status="cron_history_migrated",
        reason="Cron repair migrated isolated cron history.",
        dry_run=dry_run,
    )
    return "isolated_history_migrated"


def _existing_legacy_cron_session_id(
    conn: sqlite3.Connection, row: sqlite3.Row, address: ChatAddress
) -> str | None:
    expected_session_id = f"{address.chat_key}:cron:{row['job_id']}"
    candidates = [
        f"{row['session_key']}:cron:{row['job_id']}",
        f"{address.legacy_key}:cron:{row['job_id']}",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate == expected_session_id:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        if _count(conn, "sessions", "session_id = ?", (candidate,)) > 0:
            return candidate
    return None


def _count_cron_jobs_for_session(conn: sqlite3.Connection, session_id: str) -> int:
    if not _table_exists(conn, "cron_jobs"):
        return 0
    cron_job_id = _cron_job_id_from_session_id(session_id)
    if cron_job_id is not None:
        return _count(conn, "cron_jobs", "job_id = ?", (cron_job_id,))

    key = _parse_session_key_or_none(session_id)
    if key is not None and key.is_derived:
        return _count(conn, "cron_jobs", "session_key = ?", (session_id,))

    old_chat_key = _legacy_reference_keys(session_id)[-1]
    return _count(
        conn,
        "cron_jobs",
        "session_key = ? OR session_key = ?",
        (session_id, old_chat_key),
    )


def _count_stale_cron_references(conn: sqlite3.Connection, session_id: str) -> int:
    if not _table_exists(conn, "cron_jobs"):
        return 0
    old_chat_key = _legacy_reference_keys(session_id)[-1]
    cron_job_id = _cron_job_id_from_session_id(session_id)
    if cron_job_id is not None:
        return _count(
            conn,
            "cron_jobs",
            "job_id = ? AND (session_key = ? OR session_key = ?)",
            (cron_job_id, session_id, old_chat_key),
        )

    key = _parse_session_key_or_none(session_id)
    if key is not None and key.is_derived:
        return _count(conn, "cron_jobs", "session_key = ?", (session_id,))

    return _count(
        conn,
        "cron_jobs",
        "session_key = ? OR session_key = ?",
        (session_id, old_chat_key),
    )


def _affected_rows(conn: sqlite3.Connection, session_id: str) -> AffectedRows:
    old_chat_key = _legacy_reference_keys(session_id)[-1]
    return AffectedRows(
        sessions=_count(conn, "sessions", "session_id = ?", (session_id,)),
        memory_turns=_count(conn, "memory_turns", "session_id = ?", (session_id,)),
        active_sessions=_count(
            conn,
            "active_sessions",
            "chat_key = ? OR chat_key = ? OR session_id = ? OR session_id = ? "
            "OR session_id LIKE ? OR session_id LIKE ?",
            (
                session_id,
                old_chat_key,
                session_id,
                old_chat_key,
                f"{session_id}:%",
                f"{old_chat_key}:%",
            ),
        ),
        cron_jobs=_count_cron_jobs_for_session(conn, session_id),
        background_tasks=_count(
            conn,
            "background_tasks",
            "requester_session_id = ? OR child_session_id = ? "
            "OR child_session_id LIKE ?",
            (session_id, session_id, f"{session_id}:%"),
        ),
        memory_items=_count(
            conn,
            "memory_items",
            "scope_type = 'session' AND scope_id = ?",
            (session_id,),
        ),
        memory_item_fts=_count(
            conn,
            "memory_item_fts",
            "scope_type = 'session' AND scope_id = ?",
            (session_id,),
        ),
        memory_candidates=_count(
            conn,
            "memory_candidates",
            "scope_type = 'session' AND scope_id = ?",
            (session_id,),
        ),
    )


def _entry_already_applied(conn: sqlite3.Connection, entry: PlanEntry) -> bool:
    expected = _expected_completed_log(entry)
    if expected is None:
        return False
    status, new_session_id = expected
    if new_session_id is None:
        row = conn.execute(
            """
            SELECT 1
            FROM session_key_migration_log
            WHERE old_session_id = ? AND status = ? AND new_session_id IS NULL
            LIMIT 1
            """,
            (entry.old_session_id, status),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1
            FROM session_key_migration_log
            WHERE old_session_id = ? AND status = ? AND new_session_id = ?
            LIMIT 1
            """,
            (entry.old_session_id, status, new_session_id),
        ).fetchone()
    return row is not None


def _expected_completed_log(entry: PlanEntry) -> tuple[str, str | None] | None:
    if entry.approval == "rejected":
        return ("rejected", entry.new_session_id)
    if entry.approval == "force_keep_legacy" or (
        entry.approval == "approved" and entry.recommendation == "keep_legacy"
    ):
        return ("kept_legacy", None)
    if entry.approval == "approved" and entry.recommendation == "disable_cron":
        return ("cron_disabled", None)
    if entry.approval == "force_split":
        return ("split", None)
    if entry.approval in {"approved", "force_rename"}:
        if entry.new_session_id:
            return ("renamed", entry.new_session_id)
    return None


def _apply_entry(conn: sqlite3.Connection, entry: PlanEntry, *, dry_run: bool) -> str:
    if entry.approval == "rejected":
        _record_log(
            conn,
            old_session_id=entry.old_session_id,
            new_session_id=entry.new_session_id,
            status="rejected",
            reason=entry.notes or "Rejected by migration plan.",
            dry_run=dry_run,
        )
        _assert_no_foreign_key_errors(conn, dry_run=dry_run)
        return "rejected"

    if entry.approval == "force_keep_legacy" or (
        entry.approval == "approved" and entry.recommendation == "keep_legacy"
    ):
        _mark_legacy_untyped(conn, entry.old_session_id, dry_run=dry_run)
        _record_log(
            conn,
            old_session_id=entry.old_session_id,
            new_session_id=None,
            status="kept_legacy",
            reason=entry.notes or "Kept as legacy untyped session.",
            dry_run=dry_run,
        )
        _assert_no_foreign_key_errors(conn, dry_run=dry_run)
        return "kept_legacy"

    if entry.approval == "approved" and entry.recommendation == "disable_cron":
        disabled = _disable_cron_jobs(conn, entry.old_session_id, dry_run=dry_run)
        _mark_legacy_untyped(conn, entry.old_session_id, dry_run=dry_run)
        _record_log(
            conn,
            old_session_id=entry.old_session_id,
            new_session_id=None,
            status="cron_disabled",
            reason=entry.notes or f"Disabled {disabled} legacy cron job(s).",
            dry_run=dry_run,
        )
        _assert_no_foreign_key_errors(conn, dry_run=dry_run)
        return "cron_disabled"

    if entry.approval == "approved" and entry.recommendation == "split":
        raise ValueError("split recommendations require force_split approval")

    if entry.approval == "force_split":
        _split_session(conn, entry, dry_run=dry_run)
        _record_log(
            conn,
            old_session_id=entry.old_session_id,
            new_session_id=None,
            status="split",
            reason=entry.notes or "Split by migration plan.",
            dry_run=dry_run,
        )
        _assert_split_consistency(conn, entry, dry_run=dry_run)
        return "split"

    if entry.approval not in {"approved", "force_rename"}:
        raise ValueError(f"Unsupported approval: {entry.approval}")

    if entry.recommendation != "rename" and entry.approval != "force_rename":
        raise ValueError(
            f"Approval {entry.approval!r} cannot apply recommendation "
            f"{entry.recommendation!r}"
        )

    new_session_id = entry.new_session_id
    if not new_session_id:
        raise ValueError("new_session_id is required for rename")
    _validate_typed_session_id(new_session_id)
    _rename_session(
        conn,
        old_session_id=entry.old_session_id,
        new_session_id=new_session_id,
        dry_run=dry_run,
    )
    _record_log(
        conn,
        old_session_id=entry.old_session_id,
        new_session_id=new_session_id,
        status="renamed",
        reason=entry.notes or "Renamed by migration plan.",
        dry_run=dry_run,
    )
    _assert_rename_consistency(
        conn,
        old_session_id=entry.old_session_id,
        new_session_id=new_session_id,
        dry_run=dry_run,
    )
    return "renamed"


def _assert_rename_consistency(
    conn: sqlite3.Connection,
    *,
    old_session_id: str,
    new_session_id: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    if _count(conn, "sessions", "session_id = ?", (old_session_id,)) != 0:
        raise ValueError(f"Old session still exists after rename: {old_session_id}")
    if _count(conn, "sessions", "session_id = ?", (new_session_id,)) != 1:
        raise ValueError(f"New session is missing after rename: {new_session_id}")

    old_chat_key = _legacy_reference_keys(old_session_id)[-1]
    old_prefixes = (f"{old_session_id}:%", f"{old_chat_key}:%")
    stale_counts = {
        "memory_turns": _count(
            conn, "memory_turns", "session_id = ?", (old_session_id,)
        ),
        "active_sessions": _count(
            conn,
            "active_sessions",
            "chat_key = ? OR chat_key = ? OR session_id = ? OR session_id = ? "
            "OR session_id LIKE ? OR session_id LIKE ?",
            (
                old_session_id,
                old_chat_key,
                old_session_id,
                old_chat_key,
                old_prefixes[0],
                old_prefixes[1],
            ),
        ),
        "cron_jobs": _count_stale_cron_references(conn, old_session_id),
        "background_tasks": _count(
            conn,
            "background_tasks",
            "requester_session_id = ? OR child_session_id = ? "
            "OR requester_session_id LIKE ? OR child_session_id LIKE ?",
            (
                old_session_id,
                old_session_id,
                old_prefixes[0],
                old_prefixes[0],
            ),
        ),
        "memory_items": _count(
            conn,
            "memory_items",
            "scope_type = 'session' AND scope_id = ?",
            (old_session_id,),
        ),
        "memory_item_fts": _count(
            conn,
            "memory_item_fts",
            "scope_type = 'session' AND scope_id = ?",
            (old_session_id,),
        ),
        "memory_candidates": _count(
            conn,
            "memory_candidates",
            "scope_type = 'session' AND scope_id = ?",
            (old_session_id,),
        ),
    }
    stale = {table: count for table, count in stale_counts.items() if count}
    if stale:
        raise ValueError(f"Stale legacy references remain after rename: {stale}")
    _assert_no_foreign_key_errors(conn, dry_run=False)


def _assert_split_consistency(
    conn: sqlite3.Connection, entry: PlanEntry, *, dry_run: bool
) -> None:
    if dry_run:
        return
    if _count(conn, "sessions", "session_id = ?", (entry.old_session_id,)) != 1:
        raise ValueError(f"Legacy split shell is missing: {entry.old_session_id}")
    for target in entry.split_targets:
        if not target.turn_ids:
            continue
        if _count(conn, "sessions", "session_id = ?", (target.new_session_id,)) != 1:
            raise ValueError(
                f"Split target session is missing: {target.new_session_id}"
            )
        moved_turns = _fetch_turn_ids_for_session(
            conn, target.new_session_id, target.turn_ids
        )
        if moved_turns != set(target.turn_ids):
            missing = sorted(set(target.turn_ids) - moved_turns)
            raise ValueError(
                f"Split target {target.new_session_id} is missing turn ids: {missing}"
            )
    _assert_no_foreign_key_errors(conn, dry_run=False)


def _assert_no_foreign_key_errors(conn: sqlite3.Connection, *, dry_run: bool) -> None:
    if dry_run:
        return
    rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    if rows:
        details = [dict(row) for row in rows]
        raise ValueError(f"SQLite foreign key check failed: {details}")


def _rename_session(
    conn: sqlite3.Connection,
    *,
    old_session_id: str,
    new_session_id: str,
    dry_run: bool,
) -> None:
    old_row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (old_session_id,)
    ).fetchone()
    if old_row is None:
        raise ValueError(f"Old session does not exist: {old_session_id}")

    new_key = SessionKey.parse(new_session_id)
    address = new_key.address
    if not address.is_typed:
        raise ValueError(f"New session id is not typed: {new_session_id}")
    new_chat_key = address.chat_key
    old_key = SessionKey.parse(old_session_id)
    old_chat_key = old_key.address.legacy_key

    if dry_run:
        return

    target_row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (new_session_id,)
    ).fetchone()
    if target_row is None:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, workspace_id, created_at, last_active_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                new_session_id,
                old_row["workspace_id"],
                old_row["created_at"],
                old_row["last_active_at"],
                _metadata_for_renamed_session(old_row["metadata_json"], address),
            ),
        )
    else:
        conn.execute(
            """
            UPDATE sessions
            SET created_at = MIN(created_at, ?),
                last_active_at = MAX(last_active_at, ?),
                metadata_json = ?
            WHERE session_id = ?
            """,
            (
                old_row["created_at"],
                old_row["last_active_at"],
                _merge_target_metadata(
                    target_row["metadata_json"],
                    old_row["metadata_json"],
                    address,
                    old_session_id,
                ),
                new_session_id,
            ),
        )

    _rewrite_exact_session_references(conn, old_session_id, new_session_id)
    _rewrite_prefixed_background_tasks(conn, old_session_id, new_session_id)
    _rewrite_active_sessions(
        conn,
        old_session_id=old_session_id,
        old_chat_key=old_chat_key,
        new_session_id=new_session_id,
        new_chat_key=new_chat_key,
        address=address,
    )
    _rewrite_cron_jobs(
        conn,
        old_session_id=old_session_id,
        old_chat_key=old_chat_key,
        new_chat_key=new_chat_key,
        chat_type=address.target_type,
    )
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (old_session_id,))


def _split_session(
    conn: sqlite3.Connection, entry: PlanEntry, *, dry_run: bool
) -> None:
    old_row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (entry.old_session_id,)
    ).fetchone()
    if old_row is None:
        raise ValueError(f"Old session does not exist: {entry.old_session_id}")

    targets = [target for target in entry.split_targets if target.turn_ids]
    if not targets:
        raise ValueError("force_split requires at least one split target with turn_ids")

    seen_turn_ids: set[int] = set()
    for target in targets:
        _validate_typed_session_id(target.new_session_id)
        target_key = SessionKey.parse(target.new_session_id)
        address = ChatAddress.parse(target.chat_address)
        if not address.is_typed:
            raise ValueError(
                f"Split target address is not typed: {target.chat_address}"
            )
        if target_key.address != address:
            raise ValueError(
                f"Split target {target.new_session_id!r} does not match "
                f"chat_address {target.chat_address!r}"
            )
        duplicate_turns = seen_turn_ids.intersection(target.turn_ids)
        if duplicate_turns:
            raise ValueError(f"Duplicate split turn ids: {sorted(duplicate_turns)}")
        seen_turn_ids.update(target.turn_ids)
        current_turn_ids = _fetch_turn_ids_for_session(
            conn, entry.old_session_id, target.turn_ids
        )
        if current_turn_ids != set(target.turn_ids):
            missing = sorted(set(target.turn_ids) - current_turn_ids)
            raise ValueError(
                f"Split turn ids are not in {entry.old_session_id}: {missing}"
            )

    if dry_run:
        return

    for target in targets:
        target_key = SessionKey.parse(target.new_session_id)
        _upsert_split_target_session(
            conn,
            old_row=old_row,
            new_session_id=target.new_session_id,
            address=target_key.address,
            old_session_id=entry.old_session_id,
        )
        _move_turns(conn, target.turn_ids, target.new_session_id)

    _mark_split_remaining(conn, entry.old_session_id)


def _fetch_turn_ids_for_session(
    conn: sqlite3.Connection, session_id: str, turn_ids: list[int]
) -> set[int]:
    if not turn_ids:
        return set()
    placeholders = ", ".join("?" for _ in turn_ids)
    rows = conn.execute(
        f"""
        SELECT id
        FROM memory_turns
        WHERE session_id = ? AND id IN ({placeholders})
        """,
        (session_id, *turn_ids),
    ).fetchall()
    return {int(row["id"]) for row in rows}


def _move_turns(
    conn: sqlite3.Connection, turn_ids: list[int], new_session_id: str
) -> None:
    if not turn_ids:
        return
    placeholders = ", ".join("?" for _ in turn_ids)
    conn.execute(
        f"""
        UPDATE memory_turns
        SET session_id = ?
        WHERE id IN ({placeholders})
        """,
        (new_session_id, *turn_ids),
    )


def _upsert_split_target_session(
    conn: sqlite3.Connection,
    *,
    old_row: sqlite3.Row,
    new_session_id: str,
    address: ChatAddress,
    old_session_id: str,
) -> None:
    target_row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (new_session_id,)
    ).fetchone()
    if target_row is None:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, workspace_id, created_at, last_active_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                new_session_id,
                old_row["workspace_id"],
                old_row["created_at"],
                old_row["last_active_at"],
                _metadata_for_split_session(
                    old_row["metadata_json"], address, old_session_id
                ),
            ),
        )
        return

    conn.execute(
        """
        UPDATE sessions
        SET created_at = MIN(created_at, ?),
            last_active_at = MAX(last_active_at, ?),
            metadata_json = ?
        WHERE session_id = ?
        """,
        (
            old_row["created_at"],
            old_row["last_active_at"],
            _merge_split_target_metadata(
                target_row["metadata_json"],
                old_row["metadata_json"],
                address,
                old_session_id,
            ),
            new_session_id,
        ),
    )


def _rewrite_exact_session_references(
    conn: sqlite3.Connection, old_session_id: str, new_session_id: str
) -> None:
    conn.execute(
        "UPDATE memory_turns SET session_id = ? WHERE session_id = ?",
        (new_session_id, old_session_id),
    )
    if _table_exists(conn, "background_tasks"):
        conn.execute(
            """
            UPDATE background_tasks
            SET requester_session_id = ?
            WHERE requester_session_id = ?
            """,
            (new_session_id, old_session_id),
        )
        conn.execute(
            """
            UPDATE background_tasks
            SET child_session_id = ?
            WHERE child_session_id = ?
            """,
            (new_session_id, old_session_id),
        )
    if _table_exists(conn, "memory_items"):
        conn.execute(
            """
            UPDATE memory_items
            SET scope_id = ?
            WHERE scope_type = 'session' AND scope_id = ?
            """,
            (new_session_id, old_session_id),
        )
    if _table_exists(conn, "memory_item_fts"):
        conn.execute(
            """
            UPDATE memory_item_fts
            SET scope_id = ?
            WHERE scope_type = 'session' AND scope_id = ?
            """,
            (new_session_id, old_session_id),
        )
    if _table_exists(conn, "memory_candidates"):
        conn.execute(
            """
            UPDATE memory_candidates
            SET scope_id = ?
            WHERE scope_type = 'session' AND scope_id = ?
            """,
            (new_session_id, old_session_id),
        )


def _rewrite_prefixed_background_tasks(
    conn: sqlite3.Connection, old_session_id: str, new_session_id: str
) -> None:
    if not _table_exists(conn, "background_tasks"):
        return
    for column in ("requester_session_id", "child_session_id"):
        rows = conn.execute(
            f"""
            SELECT task_id, {column} AS value
            FROM background_tasks
            WHERE {column} LIKE ?
            """,
            (f"{old_session_id}:%",),
        ).fetchall()
        for row in rows:
            value = str(row["value"])
            rewritten = new_session_id + value[len(old_session_id) :]
            conn.execute(
                f"UPDATE background_tasks SET {column} = ? WHERE task_id = ?",
                (rewritten, row["task_id"]),
            )


def _rewrite_active_sessions(
    conn: sqlite3.Connection,
    *,
    old_session_id: str,
    old_chat_key: str,
    new_session_id: str,
    new_chat_key: str,
    address: ChatAddress,
) -> None:
    if not _table_exists(conn, "active_sessions"):
        return
    rows = conn.execute(
        """
        SELECT chat_key, session_id, updated_at
        FROM active_sessions
        WHERE chat_key = ? OR chat_key = ? OR session_id = ? OR session_id LIKE ?
        """,
        (old_session_id, old_chat_key, old_session_id, f"{old_chat_key}:%"),
    ).fetchall()
    for row in rows:
        rewritten_session_id = _rewrite_session_id_for_address(
            str(row["session_id"]), old_chat_key, address
        )
        if (
            rewritten_session_id == row["session_id"]
            and row["session_id"] == old_session_id
        ):
            rewritten_session_id = new_session_id
        conn.execute(
            "DELETE FROM active_sessions WHERE chat_key = ?",
            (row["chat_key"],),
        )
        conn.execute(
            """
            INSERT INTO active_sessions (chat_key, session_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_key) DO UPDATE SET
                session_id = excluded.session_id,
                updated_at = excluded.updated_at
            """,
            (new_chat_key, rewritten_session_id, row["updated_at"]),
        )


def _rewrite_cron_jobs(
    conn: sqlite3.Connection,
    *,
    old_session_id: str,
    old_chat_key: str,
    new_chat_key: str,
    chat_type: str,
) -> None:
    if not _table_exists(conn, "cron_jobs"):
        return
    cron_job_id = _cron_job_id_from_session_id(old_session_id)
    if cron_job_id is not None:
        if _column_exists(conn, "cron_jobs", "chat_type"):
            conn.execute(
                """
                UPDATE cron_jobs
                SET session_key = ?, chat_type = ?
                WHERE job_id = ?
                """,
                (new_chat_key, chat_type, cron_job_id),
            )
        else:
            conn.execute(
                """
                UPDATE cron_jobs
                SET session_key = ?
                WHERE job_id = ?
                """,
                (new_chat_key, cron_job_id),
            )
        return

    key = _parse_session_key_or_none(old_session_id)
    if key is not None and key.is_derived:
        if _column_exists(conn, "cron_jobs", "chat_type"):
            conn.execute(
                """
                UPDATE cron_jobs
                SET session_key = ?, chat_type = ?
                WHERE session_key = ?
                """,
                (new_chat_key, chat_type, old_session_id),
            )
        else:
            conn.execute(
                """
                UPDATE cron_jobs
                SET session_key = ?
                WHERE session_key = ?
                """,
                (new_chat_key, old_session_id),
            )
        return

    if _column_exists(conn, "cron_jobs", "chat_type"):
        conn.execute(
            """
            UPDATE cron_jobs
            SET session_key = ?, chat_type = ?
            WHERE session_key = ? OR session_key = ?
            """,
            (new_chat_key, chat_type, old_session_id, old_chat_key),
        )
    else:
        conn.execute(
            """
            UPDATE cron_jobs
            SET session_key = ?
            WHERE session_key = ? OR session_key = ?
            """,
            (new_chat_key, old_session_id, old_chat_key),
        )


def _disable_cron_jobs(
    conn: sqlite3.Connection, session_id: str, *, dry_run: bool
) -> int:
    if dry_run or not _table_exists(conn, "cron_jobs"):
        return 0
    assignments = ["is_active = 0"]
    params: list[object] = []
    if _column_exists(conn, "cron_jobs", "claimed_at"):
        assignments.append("claimed_at = NULL")
    if _column_exists(conn, "cron_jobs", "last_error"):
        assignments.append("last_error = ?")
        params.append(
            "Disabled by session key migration because target type is unknown."
        )
    key = _parse_session_key_or_none(session_id)
    if key is not None and key.is_derived:
        cron_job_id = _cron_job_id_from_session_id(session_id)
        if cron_job_id is not None:
            where_sql = "job_id = ?"
            params.append(cron_job_id)
        else:
            where_sql = "session_key = ?"
            params.append(session_id)
    else:
        keys = _legacy_reference_keys(session_id)
        placeholders = ", ".join("?" for _ in keys)
        where_sql = f"session_key IN ({placeholders})"
        params.extend(keys)
    cursor = conn.execute(
        f"""
        UPDATE cron_jobs
        SET {", ".join(assignments)}
        WHERE {where_sql}
        """,
        tuple(params),
    )
    return cursor.rowcount


def _legacy_reference_keys(session_id: str) -> tuple[str, ...]:
    try:
        old_key = SessionKey.parse(session_id)
    except ValueError:
        return (session_id,)
    old_chat_key = old_key.address.legacy_key
    if old_chat_key == session_id:
        return (session_id,)
    return (session_id, old_chat_key)


def _parse_session_key_or_none(session_id: str) -> SessionKey | None:
    try:
        return SessionKey.parse(session_id)
    except ValueError:
        return None


def _cron_job_id_from_session_id(session_id: str) -> str | None:
    """Extract the job id from isolated cron-run session ids.

    The runtime shape is ``<chat_key>:cron:<job_id>``.
    """
    key = _parse_session_key_or_none(session_id)
    if key is None or not key.suffix:
        return None
    parts = key.suffix.split(":")
    if len(parts) >= 2 and parts[0] == "cron" and parts[1]:
        return ":".join(parts[1:])
    return None


def _rewrite_session_id_for_address(
    value: str, old_chat_key: str, address: ChatAddress
) -> str:
    if value == old_chat_key:
        return address.chat_key
    if value.startswith(f"{old_chat_key}:"):
        return f"{address.chat_key}{value[len(old_chat_key) :]}"
    return value


def _mark_legacy_untyped(
    conn: sqlite3.Connection, session_id: str, *, dry_run: bool
) -> None:
    if dry_run:
        return
    row = conn.execute(
        "SELECT metadata_json FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Session does not exist: {session_id}")
    metadata = _json_dict(row["metadata_json"])
    metadata["legacy_untyped"] = True
    metadata["session_key_migration"] = {
        "status": "kept_legacy",
        "updated_at": _utc_now(),
    }
    conn.execute(
        "UPDATE sessions SET metadata_json = ? WHERE session_id = ?",
        (_json_dump(metadata), session_id),
    )


def _mark_split_remaining(conn: sqlite3.Connection, session_id: str) -> None:
    row = conn.execute(
        "SELECT metadata_json FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Session does not exist: {session_id}")
    metadata = _json_dict(row["metadata_json"])
    metadata["legacy_untyped"] = True
    metadata["session_key_migration"] = {
        "status": "split_remaining",
        "updated_at": _utc_now(),
    }
    conn.execute(
        "UPDATE sessions SET metadata_json = ? WHERE session_id = ?",
        (_json_dump(metadata), session_id),
    )


def _metadata_for_renamed_session(raw_metadata: object, address: ChatAddress) -> str:
    metadata = _json_dict(raw_metadata)
    metadata["chat_address"] = _chat_address_dict(address)
    metadata["session_key_migration"] = {
        "status": "renamed",
        "updated_at": _utc_now(),
    }
    return _json_dump(metadata)


def _metadata_for_split_session(
    raw_metadata: object, address: ChatAddress, old_session_id: str
) -> str:
    metadata = _json_dict(raw_metadata)
    metadata["chat_address"] = _chat_address_dict(address)
    metadata["session_key_migration"] = {
        "status": "split_from_legacy",
        "old_session_id": old_session_id,
        "updated_at": _utc_now(),
    }
    return _json_dump(metadata)


def _merge_target_metadata(
    target_raw: object, old_raw: object, address: ChatAddress, old_session_id: str
) -> str:
    merged = _json_dict(old_raw)
    merged.update(_json_dict(target_raw))
    migrated_from = merged.get("migrated_from")
    if not isinstance(migrated_from, list):
        migrated_from = []
    if old_session_id not in migrated_from:
        migrated_from.append(old_session_id)
    merged["migrated_from"] = migrated_from
    merged["chat_address"] = _chat_address_dict(address)
    merged["session_key_migration"] = {
        "status": "merged_rename",
        "updated_at": _utc_now(),
    }
    return _json_dump(merged)


def _merge_split_target_metadata(
    target_raw: object, old_raw: object, address: ChatAddress, old_session_id: str
) -> str:
    merged = _json_dict(old_raw)
    merged.update(_json_dict(target_raw))
    split_from = merged.get("split_from")
    if not isinstance(split_from, list):
        split_from = []
    if old_session_id not in split_from:
        split_from.append(old_session_id)
    merged["split_from"] = split_from
    merged["chat_address"] = _chat_address_dict(address)
    merged["session_key_migration"] = {
        "status": "merged_split",
        "updated_at": _utc_now(),
    }
    return _json_dump(merged)


def _record_log(
    conn: sqlite3.Connection,
    *,
    old_session_id: str,
    new_session_id: str | None,
    status: str,
    reason: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    _ensure_migration_log(conn)
    conn.execute(
        """
        INSERT INTO session_key_migration_log (
            old_session_id, new_session_id, status, reason, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (old_session_id, new_session_id, status, reason, _utc_now()),
    )


def _validate_typed_session_id(value: str) -> None:
    kind = classify_session_key(value)
    if not kind.startswith("typed"):
        raise ValueError(f"Expected typed session id, got {value!r}")


def _summarize_entries(entries: list[PlanEntry]) -> dict[str, int]:
    summary = Counter[str]()
    summary["total"] = len(entries)
    for entry in entries:
        summary[entry.recommendation] += 1
        if entry.recommendation == "rename" and entry.confidence == "high":
            summary["rename_high"] += 1
        if entry.recommendation in {"manual_review", "split"}:
            summary["manual_review"] += 1
    return dict(summary)


def _plan_to_dict(plan: MigrationPlan) -> dict[str, Any]:
    return {
        "version": plan.version,
        "generated_at": plan.generated_at,
        "db_path": plan.db_path,
        "summary": dict(plan.summary),
        "entries": [_entry_to_dict(entry) for entry in plan.entries],
    }


def _entry_to_dict(entry: PlanEntry) -> dict[str, Any]:
    data = asdict(entry)
    data["affected"] = asdict(entry.affected)
    data["evidence"] = [asdict(item) for item in entry.evidence]
    data["split_targets"] = [asdict(item) for item in entry.split_targets]
    return data


def _load_plan(path: Path) -> MigrationPlan:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("version", 0)) != PLAN_VERSION:
        raise ValueError(f"Unsupported plan version: {raw.get('version')!r}")
    entries = [_entry_from_dict(item) for item in raw.get("entries", [])]
    return MigrationPlan(
        version=int(raw["version"]),
        generated_at=str(raw.get("generated_at") or ""),
        db_path=str(raw.get("db_path") or ""),
        summary=dict(raw.get("summary") or {}),
        entries=entries,
    )


def _entry_from_dict(raw: dict[str, Any]) -> PlanEntry:
    return PlanEntry(
        old_session_id=str(raw["old_session_id"]),
        status=str(raw.get("status") or "needs_approval"),
        recommendation=raw["recommendation"],
        new_session_id=raw.get("new_session_id"),
        confidence=raw.get("confidence", "low"),
        evidence=[
            Evidence(
                source=str(item.get("source") or ""),
                chat_address=str(item.get("chat_address") or ""),
                turn_count=int(item.get("turn_count") or 0),
                details=dict(item.get("details") or {}),
            )
            for item in raw.get("evidence", [])
            if isinstance(item, dict)
        ],
        affected=AffectedRows(**dict(raw.get("affected") or {})),
        approval=raw.get("approval", "pending"),
        notes=str(raw.get("notes") or ""),
        split_targets=[
            SplitTarget(
                chat_address=str(item.get("chat_address") or ""),
                new_session_id=str(item.get("new_session_id") or ""),
                turn_ids=[int(turn_id) for turn_id in item.get("turn_ids", [])],
            )
            for item in raw.get("split_targets", [])
            if isinstance(item, dict)
        ],
    )


def _connect(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_name(
        f"{db_path.name}.session-key-migration.{timestamp}.bak"
    )
    shutil.copy2(db_path, backup_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.exists():
            shutil.copy2(sidecar, Path(f"{backup_path}{suffix}"))
    return backup_path


def _ensure_migration_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_key_migration_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_session_id TEXT NOT NULL,
            new_session_id TEXT,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _count(
    conn: sqlite3.Connection, table: str, where: str, params: tuple[object, ...]
) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", params)
    return int(row.fetchone()["count"])


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'view') AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    return any(
        row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})")
    )


def _json_dict(raw: object) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _chat_address_dict(address: ChatAddress) -> dict[str, str]:
    result = {
        "channel": address.channel,
        "target_type": address.target_type,
        "target_id": address.target_id,
    }
    if address.thread_id:
        result["thread_id"] = address.thread_id
    return result


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and apply typed session-key migrations."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect_cmd = subcommands.add_parser("inspect", help="Generate a migration plan")
    inspect_cmd.add_argument("--db", required=True, type=Path, help="SQLite db path")
    inspect_cmd.add_argument("--out", required=True, type=Path, help="Plan JSON output")
    inspect_cmd.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for approvals before writing the plan",
    )

    apply_cmd = subcommands.add_parser("apply", help="Apply approved plan entries")
    apply_cmd.add_argument("--db", required=True, type=Path, help="SQLite db path")
    apply_cmd.add_argument("--plan", required=True, type=Path, help="Plan JSON path")
    apply_cmd.add_argument(
        "--dry-run", action="store_true", help="Do not write changes"
    )
    apply_cmd.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a backup before modifying the database",
    )
    apply_cmd.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first failed plan entry",
    )

    repair_cron_cmd = subcommands.add_parser(
        "repair-cron",
        help="Repair cron chat_type/session_key rows after migration",
    )
    repair_cron_cmd.add_argument(
        "--db", required=True, type=Path, help="SQLite db path"
    )
    repair_cron_cmd.add_argument(
        "--dry-run", action="store_true", help="Do not write changes"
    )
    repair_cron_cmd.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a backup before modifying the database",
    )
    repair_cron_cmd.add_argument(
        "--migrate-history",
        choices=("none", "active", "all"),
        default="none",
        help=(
            "Move legacy isolated cron history to typed cron sessions: "
            "none (default), active jobs only, or all jobs"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "inspect":
        plan = inspect_database(args.db)
        if args.interactive:
            stats = review_plan_interactively(plan)
            print(
                "Interactive approvals: "
                f"{stats.get('approved', 0)} approved, "
                f"{stats.get('pending', 0)} left pending, "
                f"{stats.get('manual_review', 0)} manual review"
            )
        write_plan(plan, args.out)
        print_summary(plan.summary)
        print(f"Plan written: {args.out}")
        return 0
    if args.command == "apply":
        summary = apply_plan(
            db_path=args.db,
            plan_path=args.plan,
            dry_run=args.dry_run,
            backup=not args.no_backup,
            stop_on_error=args.stop_on_error,
        )
        print_apply_summary(summary)
        return 0
    if args.command == "repair-cron":
        summary = repair_cron_sessions(
            db_path=args.db,
            dry_run=args.dry_run,
            backup=not args.no_backup,
            migrate_history=cast(CronHistoryMode, args.migrate_history),
        )
        print_cron_repair_summary(summary)
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
