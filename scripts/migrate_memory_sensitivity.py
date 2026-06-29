"""Inspect and apply the memory sensitivity soft-default backfill (Piece A1).

Legacy ``memory_items`` were written with ``sensitivity='private'`` as an
unthinking system default — old code never classified sensitivity. The new
soft-scope model treats ``public`` as the recallable baseline and reserves
``private`` / ``secret_like`` for items explicitly or auto-tagged as restricted
(``sensitivity_source`` in ``{dream, explicit}``). This script reinterprets the
legacy default: items with ``sensitivity='private' AND sensitivity_source='default'``
are flipped to ``public`` (soft). Everything else is left untouched.

Conservative workflow (mirrors ``migrate_memory_scope.py``):

1. Inspect production data and write a plan::

       uv run python scripts/migrate_memory_sensitivity.py inspect \\
           --db data/nahida.db --plan sensitivity-plan.json

2. Review the plan. Either mark chosen entries ``"approval": "approved"`` or
   use ``apply --apply-all-safe`` for every ``flip_to_public`` entry.

3. Apply with an automatic SQLite backup::

       uv run python scripts/migrate_memory_sensitivity.py apply \\
           --db data/nahida.db --plan sensitivity-plan.json --confirm

Run this BEFORE enabling ``memory.retrieval.soft_scope`` (Piece A2).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

PLAN_VERSION = 1

Action = Literal["flip_to_public", "keep"]
Approval = Literal["pending", "approved", "rejected"]


@dataclass(slots=True)
class PlanEntry:
    item_id: str
    kind: str
    current_sensitivity: str
    current_source: str
    action: Action
    reason: str
    approval: Approval = "pending"


@dataclass(slots=True)
class MigrationPlan:
    version: int
    generated_at: str
    db_path: str
    summary: dict[str, int]
    entries: list[PlanEntry]


def inspect_database(*, db_path: Path) -> MigrationPlan:
    """Build a read-only backfill plan for legacy default-private items."""
    conn = _connect(db_path, read_only=True)
    try:
        _require_table(conn, "memory_items")
        _require_sensitivity_source_column(conn)
        rows = conn.execute(
            """
            SELECT item_id, kind, sensitivity, sensitivity_source
            FROM memory_items
            ORDER BY item_id
            """
        ).fetchall()
        entries = [_inspect_row(row) for row in rows]
        return MigrationPlan(
            version=PLAN_VERSION,
            generated_at=_utc_now(),
            db_path=str(db_path),
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
    """Apply approved flip_to_public entries from a prior inspect plan."""
    if not dry_run and not confirm:
        raise ValueError("apply requires --confirm unless --dry-run is set")
    plan = load_plan(plan_path)
    if not dry_run and backup and str(db_path) != ":memory:":
        backup_path = _backup_database(db_path)
        print(f"Backup written: {backup_path}")

    conn = _connect(db_path, read_only=False)
    try:
        _require_table(conn, "memory_items")
        _require_sensitivity_source_column(conn)
        results: Counter[str] = Counter()
        try:
            conn.execute("BEGIN")
            _ensure_migration_log(conn)
            for entry in plan.entries:
                if entry.action != "flip_to_public":
                    results[entry.action] += 1
                    continue
                if entry.approval != "approved" and not apply_all_safe:
                    results["pending"] += 1
                    continue
                if _entry_already_applied(conn, entry):
                    results["already_applied"] += 1
                    continue
                _apply_entry(conn, entry)
                results["flipped"] += 1
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(plan), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def load_plan(path: Path) -> MigrationPlan:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = [
        PlanEntry(
            item_id=str(entry.get("item_id", "")),
            kind=str(entry.get("kind", "")),
            current_sensitivity=str(entry.get("current_sensitivity", "")),
            current_source=str(entry.get("current_source", "")),
            action=str(entry.get("action", "keep")),  # type: ignore[arg-type]
            reason=str(entry.get("reason", "")),
            approval=str(entry.get("approval", "pending")),  # type: ignore[arg-type]
        )
        for entry in raw.get("entries", [])
        if isinstance(entry, dict)
    ]
    return MigrationPlan(
        version=int(raw.get("version", 0)),
        generated_at=str(raw.get("generated_at", "")),
        db_path=str(raw.get("db_path", "")),
        summary={
            str(key): int(value) for key, value in dict(raw.get("summary", {})).items()
        },
        entries=entries,
    )


def _inspect_row(row: sqlite3.Row) -> PlanEntry:
    item_id = str(row["item_id"])
    kind = str(row["kind"])
    sensitivity = str(row["sensitivity"])
    source = str(row["sensitivity_source"])
    if sensitivity == "private" and source == "default":
        return PlanEntry(
            item_id=item_id,
            kind=kind,
            current_sensitivity=sensitivity,
            current_source=source,
            action="flip_to_public",
            reason="legacy system-default private → soft public",
        )
    return PlanEntry(
        item_id=item_id,
        kind=kind,
        current_sensitivity=sensitivity,
        current_source=source,
        action="keep",
        reason=f"sensitivity={sensitivity!r} source={source!r} (not a default-private)",
    )


def _apply_entry(conn: sqlite3.Connection, entry: PlanEntry) -> None:
    cursor = conn.execute(
        """
        UPDATE memory_items
        SET sensitivity = 'public'
        WHERE item_id = ? AND sensitivity = ? AND sensitivity_source = ?
        """,
        (entry.item_id, entry.current_sensitivity, entry.current_source),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(
            f"Stale plan for {entry.item_id}: no longer "
            f"{entry.current_sensitivity}/{entry.current_source}"
        )
    conn.execute(
        """
        INSERT INTO memory_sensitivity_migration_log
        (item_id, old_sensitivity, old_source, new_sensitivity, reason, applied_at)
        VALUES (?, ?, ?, 'public', ?, ?)
        """,
        (
            entry.item_id,
            entry.current_sensitivity,
            entry.current_source,
            entry.reason,
            _utc_now(),
        ),
    )


def _entry_already_applied(conn: sqlite3.Connection, entry: PlanEntry) -> bool:
    row = conn.execute(
        "SELECT 1 FROM memory_items WHERE item_id = ? AND sensitivity = 'public'",
        (entry.item_id,),
    ).fetchone()
    return row is not None


def _ensure_migration_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_sensitivity_migration_log (
            item_id TEXT PRIMARY KEY,
            old_sensitivity TEXT NOT NULL,
            old_source TEXT NOT NULL,
            new_sensitivity TEXT NOT NULL,
            reason TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _require_sensitivity_source_column(conn: sqlite3.Connection) -> None:
    """The column is added at boot by DatabaseEngine; refuse to run against a
    DB that hasn't been opened by the app (column would be missing)."""
    cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(memory_items)")}
    if "sensitivity_source" not in cols:
        raise RuntimeError(
            "memory_items.sensitivity_source column missing — open the DB with "
            "the app once (it adds the column idempotently) before running this."
        )


def _backup_database(db_path: Path) -> Path:
    """WAL-safe snapshot of the database to a timestamped sidecar.

    The engine runs ``PRAGMA journal_mode=WAL``, so a raw ``shutil.copy2`` of
    the main file misses uncheckpointed pages held in the ``-wal`` sidecar —
    the backup would silently be stale/consistent-only-by-luck. SQLite's online
    backup API copies a transactionally consistent snapshot (WAL pages
    included) without requiring the bot to be stopped or checkpointed.
    """
    if str(db_path) == ":memory:" or not db_path.exists():
        raise RuntimeError(f"cannot back up non-file database: {db_path}")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_suffix(
        db_path.suffix + f".memory-sensitivity-{timestamp}.bak"
    )
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
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
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="Build a backfill plan")
    inspect_parser.add_argument("--db", type=Path, required=True)
    inspect_parser.add_argument("--plan", type=Path, required=True)

    apply_parser = sub.add_parser("apply", help="Apply an approved plan")
    apply_parser.add_argument("--db", type=Path, required=True)
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.add_argument("--confirm", action="store_true")
    apply_parser.add_argument("--no-backup", action="store_true")
    apply_parser.add_argument(
        "--apply-all-safe",
        action="store_true",
        help="Apply every plan entry whose generated action is flip_to_public.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "inspect":
            plan = inspect_database(db_path=args.db)
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
        print(f"memory sensitivity migration failed: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
