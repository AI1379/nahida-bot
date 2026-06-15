"""Inspect and apply durable-memory scope migrations.

This script is intentionally conservative. It only migrates old global
``memory_items`` when a typed chat scope can be inferred from structured
metadata/evidence. Content text is never used to guess ownership.

Typical workflow:

1. Inspect production data and write a plan:

   uv run python scripts/migrate_memory_scope.py inspect --db data/nahida.db --plan memory-scope-plan.json

2. Review the plan. Either mark chosen entries as ``"approval": "approved"``
   or use ``apply --apply-all-safe`` for all generated migrate actions.

3. Apply with an automatic SQLite backup:

   uv run python scripts/migrate_memory_scope.py apply --db data/nahida.db --plan memory-scope-plan.json --confirm
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from nahida_bot.agent.memory.scope import (
    CHAT_SCOPED_KINDS,
    SCOPE_ID_GLOBAL,
    SCOPE_TYPE_CHAT,
    SCOPE_TYPE_GLOBAL,
    resolve_scope_from_session,
)
from nahida_bot.core.chat_address import ChatAddress

PLAN_VERSION = 1

Action = Literal["migrate", "keep_global", "skip_non_global", "manual_review"]
Approval = Literal["pending", "approved", "rejected"]

_SESSION_ID_KEYS = frozenset(
    {
        "session_id",
        "source_session_id",
        "created_from_session_id",
        "requester_session_id",
    }
)
_CHAT_ADDRESS_KEYS = frozenset(
    {
        "chat_address",
        "source_chat_address",
        "created_from_chat_address",
        "target_chat_address",
        "from_chat_address",
    }
)


@dataclass(frozen=True, slots=True)
class ScopeEvidence:
    source: str
    value: str
    scope_type: str
    scope_id: str


@dataclass(slots=True)
class PlanEntry:
    item_id: str
    kind: str
    status: str
    current_scope_type: str
    current_scope_id: str
    action: Action
    reason: str
    target_scope_type: str = ""
    target_scope_id: str = ""
    evidence: list[ScopeEvidence] = field(default_factory=list)
    approval: Approval = "pending"


@dataclass(slots=True)
class MigrationPlan:
    version: int
    generated_at: str
    db_path: str
    include_archived: bool
    summary: dict[str, int]
    entries: list[PlanEntry]


def inspect_database(*, db_path: Path, include_archived: bool = False) -> MigrationPlan:
    """Build a read-only migration plan for global durable memory items."""
    conn = _connect(db_path, read_only=True)
    try:
        _require_table(conn, "memory_items")
        _require_table(conn, "memory_item_fts")
        rows = conn.execute(
            """
            SELECT item_id, kind, status, scope_type, scope_id, evidence_json,
                   metadata_json
            FROM memory_items
            WHERE (? OR status = 'active')
            ORDER BY updated_at DESC, item_id
            """,
            (1 if include_archived else 0,),
        ).fetchall()
        entries = [_inspect_row(row) for row in rows]
        return MigrationPlan(
            version=PLAN_VERSION,
            generated_at=_utc_now(),
            db_path=str(db_path),
            include_archived=include_archived,
            summary=dict(Counter(entry.action for entry in entries)),
            entries=entries,
        )
    finally:
        conn.close()


def apply_plan(
    *,
    db_path: Path,
    plan_path: Path,
    dry_run: bool = False,
    confirm: bool = False,
    backup: bool = True,
    apply_all_safe: bool = False,
) -> dict[str, int]:
    """Apply approved migrate entries from a prior inspect plan."""
    if not dry_run and not confirm:
        raise ValueError("apply requires --confirm unless --dry-run is set")
    plan = load_plan(plan_path)
    if not dry_run and backup and str(db_path) != ":memory:":
        backup_path = _backup_database(db_path)
        print(f"Backup written: {backup_path}")

    conn = _connect(db_path, read_only=False)
    try:
        _require_table(conn, "memory_items")
        _require_table(conn, "memory_item_fts")
        results = Counter[str]()
        try:
            conn.execute("BEGIN")
            _ensure_migration_log(conn)
            for entry in plan.entries:
                if entry.action != "migrate":
                    results[entry.action] += 1
                    continue
                if entry.approval != "approved" and not apply_all_safe:
                    results["pending"] += 1
                    continue
                if _entry_already_applied(conn, entry):
                    results["already_applied"] += 1
                    continue
                _apply_entry(conn, entry)
                results["migrated"] += 1
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        return dict(results)
    finally:
        conn.close()


def write_plan(plan: MigrationPlan, path: Path) -> None:
    """Write a migration plan as reviewable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(plan), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def load_plan(path: Path) -> MigrationPlan:
    """Load a migration plan from JSON."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = [
        PlanEntry(
            evidence=[
                ScopeEvidence(
                    source=str(ev.get("source", "")),
                    value=str(ev.get("value", "")),
                    scope_type=str(ev.get("scope_type", "")),
                    scope_id=str(ev.get("scope_id", "")),
                )
                for ev in entry.get("evidence", [])
                if isinstance(ev, dict)
            ],
            item_id=str(entry.get("item_id", "")),
            kind=str(entry.get("kind", "")),
            status=str(entry.get("status", "")),
            current_scope_type=str(entry.get("current_scope_type", "")),
            current_scope_id=str(entry.get("current_scope_id", "")),
            action=str(entry.get("action", "manual_review")),  # type: ignore[arg-type]
            reason=str(entry.get("reason", "")),
            target_scope_type=str(entry.get("target_scope_type", "")),
            target_scope_id=str(entry.get("target_scope_id", "")),
            approval=str(entry.get("approval", "pending")),  # type: ignore[arg-type]
        )
        for entry in raw.get("entries", [])
        if isinstance(entry, dict)
    ]
    return MigrationPlan(
        version=int(raw.get("version", 0)),
        generated_at=str(raw.get("generated_at", "")),
        db_path=str(raw.get("db_path", "")),
        include_archived=bool(raw.get("include_archived", False)),
        summary={
            str(key): int(value) for key, value in dict(raw.get("summary", {})).items()
        },
        entries=entries,
    )


def _inspect_row(row: sqlite3.Row) -> PlanEntry:
    item_id = str(row["item_id"])
    kind = str(row["kind"])
    status = str(row["status"])
    scope_type = str(row["scope_type"])
    scope_id = str(row["scope_id"])
    if scope_type != SCOPE_TYPE_GLOBAL or scope_id != SCOPE_ID_GLOBAL:
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            status=status,
            current_scope_type=scope_type,
            current_scope_id=scope_id,
            action="skip_non_global",
            reason="item already has a non-global scope",
        )
    if kind not in CHAT_SCOPED_KINDS:
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            status=status,
            current_scope_type=scope_type,
            current_scope_id=scope_id,
            action="keep_global",
            reason=f"kind {kind!r} is treated as shared/global",
        )

    metadata = _loads_json(row["metadata_json"])
    evidence_json = _loads_json(row["evidence_json"])
    evidence = [
        *_extract_scope_evidence(metadata, source="metadata"),
        *_extract_scope_evidence(evidence_json, source="evidence"),
    ]
    chat_scopes = sorted(
        {
            (ev.scope_type, ev.scope_id)
            for ev in evidence
            if ev.scope_type == SCOPE_TYPE_CHAT and ev.scope_id
        }
    )
    if len(chat_scopes) == 1:
        target_scope_type, target_scope_id = chat_scopes[0]
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            status=status,
            current_scope_type=scope_type,
            current_scope_id=scope_id,
            action="migrate",
            reason="single typed chat scope inferred from structured evidence",
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
            evidence=evidence,
        )
    if len(chat_scopes) > 1:
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            status=status,
            current_scope_type=scope_type,
            current_scope_id=scope_id,
            action="manual_review",
            reason="conflicting typed chat scopes found",
            evidence=evidence,
        )
    return PlanEntry(
        item_id=item_id,
        kind=kind,
        status=status,
        current_scope_type=scope_type,
        current_scope_id=scope_id,
        action="keep_global",
        reason="no typed chat scope found in structured evidence",
        evidence=evidence,
    )


def _extract_scope_evidence(value: object, *, source: str) -> list[ScopeEvidence]:
    evidence: list[ScopeEvidence] = []

    def visit(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            maybe_context = obj.get("message_context")
            if isinstance(maybe_context, dict):
                context_ev = _evidence_from_message_context(
                    maybe_context,
                    source=f"{path}.message_context",
                )
                if context_ev is not None:
                    evidence.append(context_ev)
            for key, child in obj.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}"
                if isinstance(child, str):
                    if key_text in _SESSION_ID_KEYS:
                        ev = _evidence_from_session_id(child, source=child_path)
                        if ev is not None:
                            evidence.append(ev)
                    elif key_text in _CHAT_ADDRESS_KEYS:
                        ev = _evidence_from_chat_address(child, source=child_path)
                        if ev is not None:
                            evidence.append(ev)
                if isinstance(child, dict | list):
                    visit(child, child_path)
        elif isinstance(obj, list):
            for index, child in enumerate(obj):
                if isinstance(child, dict | list):
                    visit(child, f"{path}[{index}]")

    visit(value, source)
    return _dedupe_evidence(evidence)


def _evidence_from_message_context(
    data: dict[str, Any],
    *,
    source: str,
) -> ScopeEvidence | None:
    channel = str(data.get("channel") or "").strip()
    chat_type = str(data.get("chat_type") or "").strip()
    chat_id = str(data.get("chat_id") or "").strip()
    if not channel or not chat_type or not chat_id or chat_type == "unknown":
        return None
    try:
        address = ChatAddress(channel=channel, target_type=chat_type, target_id=chat_id)
    except ValueError:
        return None
    if not address.is_typed:
        return None
    return ScopeEvidence(
        source=source,
        value=str(address),
        scope_type=SCOPE_TYPE_CHAT,
        scope_id=address.chat_key,
    )


def _evidence_from_session_id(value: str, *, source: str) -> ScopeEvidence | None:
    scope_type, scope_id = resolve_scope_from_session(value)
    if scope_type != SCOPE_TYPE_CHAT:
        return None
    return ScopeEvidence(
        source=source,
        value=value,
        scope_type=scope_type,
        scope_id=scope_id,
    )


def _evidence_from_chat_address(value: str, *, source: str) -> ScopeEvidence | None:
    try:
        address = ChatAddress.parse(value)
    except ValueError:
        return _evidence_from_session_id(value, source=source)
    if not address.is_typed:
        return None
    return ScopeEvidence(
        source=source,
        value=value,
        scope_type=SCOPE_TYPE_CHAT,
        scope_id=address.chat_key,
    )


def _dedupe_evidence(items: list[ScopeEvidence]) -> list[ScopeEvidence]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[ScopeEvidence] = []
    for item in items:
        key = (item.source, item.value, item.scope_type, item.scope_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _loads_json(value: object) -> object:
    if not isinstance(value, str) or not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _apply_entry(conn: sqlite3.Connection, entry: PlanEntry) -> None:
    now = _utc_now()
    cursor = conn.execute(
        """
        UPDATE memory_items
        SET scope_type = ?, scope_id = ?
        WHERE item_id = ? AND scope_type = ? AND scope_id = ?
        """,
        (
            entry.target_scope_type,
            entry.target_scope_id,
            entry.item_id,
            entry.current_scope_type,
            entry.current_scope_id,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(
            "Stale migration plan for "
            f"{entry.item_id}: current scope is no longer "
            f"{entry.current_scope_type}:{entry.current_scope_id}"
        )
    conn.execute(
        """
        UPDATE memory_item_fts
        SET scope_type = ?, scope_id = ?
        WHERE item_id = ?
        """,
        (entry.target_scope_type, entry.target_scope_id, entry.item_id),
    )
    conn.execute(
        """
        INSERT INTO memory_scope_migration_log
        (item_id, old_scope_type, old_scope_id, new_scope_type, new_scope_id,
         reason, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.item_id,
            entry.current_scope_type,
            entry.current_scope_id,
            entry.target_scope_type,
            entry.target_scope_id,
            entry.reason,
            now,
        ),
    )


def _entry_already_applied(conn: sqlite3.Connection, entry: PlanEntry) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM memory_items
        WHERE item_id = ? AND scope_type = ? AND scope_id = ?
        """,
        (entry.item_id, entry.target_scope_type, entry.target_scope_id),
    ).fetchone()
    return row is not None


def _ensure_migration_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_scope_migration_log (
            item_id TEXT PRIMARY KEY,
            old_scope_type TEXT NOT NULL,
            old_scope_id TEXT NOT NULL,
            new_scope_type TEXT NOT NULL,
            new_scope_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_suffix(db_path.suffix + f".memory-scope-{timestamp}.bak")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _connect(db_path: Path, *, read_only: bool) -> sqlite3.Connection:
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    elif read_only:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _require_table(conn: sqlite3.Connection, name: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Required table not found: {name}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _print_summary(plan: MigrationPlan | dict[str, int]) -> None:
    summary = plan.summary if isinstance(plan, MigrationPlan) else plan
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Build a migration plan")
    inspect_parser.add_argument("--db", type=Path, required=True)
    inspect_parser.add_argument("--plan", type=Path, required=True)
    inspect_parser.add_argument("--include-archived", action="store_true")

    apply_parser = subparsers.add_parser("apply", help="Apply an approved plan")
    apply_parser.add_argument("--db", type=Path, required=True)
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.add_argument("--confirm", action="store_true")
    apply_parser.add_argument("--no-backup", action="store_true")
    apply_parser.add_argument(
        "--apply-all-safe",
        action="store_true",
        help="Apply every plan entry whose generated action is migrate.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "inspect":
            plan = inspect_database(
                db_path=args.db,
                include_archived=bool(args.include_archived),
            )
            write_plan(plan, args.plan)
            print(f"Plan written: {args.plan}")
            _print_summary(plan)
            return 0
        if args.command == "apply":
            result = apply_plan(
                db_path=args.db,
                plan_path=args.plan,
                dry_run=bool(args.dry_run),
                confirm=bool(args.confirm),
                backup=not bool(args.no_backup),
                apply_all_safe=bool(args.apply_all_safe),
            )
            _print_summary(result)
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"memory scope migration failed: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
