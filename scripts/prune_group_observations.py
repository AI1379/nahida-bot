"""Prune expired observed-only group messages from the SQLite memory store.

Observed group messages are persisted with ``source = 'group_observation'``.
They are only used as short-lived context for a later group trigger, so this
script deliberately leaves normal conversation turns, private chats, and
plugin/system records untouched.

The script defaults to a dry run. Use ``--apply`` to delete rows and
``--vacuum`` only while the bot is stopped if the database file itself must
shrink.

Examples
--------

    uv run python scripts/prune_group_observations.py \
        --db data/nahida.db --older-than-days 7

    uv run python scripts/prune_group_observations.py \
        --db data/nahida.db --older-than-days 7 --apply

    uv run python scripts/prune_group_observations.py \
        --db data/nahida.db --older-than-days 7 --apply --vacuum
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


OBSERVATION_SOURCE = "group_observation"
DEFAULT_BATCH_SIZE = 500
_REQUIRED_TABLES = ("memory_turns", "memory_keywords")


@dataclass(frozen=True, slots=True)
class PrunePlan:
    """Summary of observation rows eligible for deletion."""

    cutoff: datetime
    turn_count: int
    content_chars: int
    metadata_chars: int
    oldest_created_at: str | None
    newest_created_at: str | None


@dataclass(frozen=True, slots=True)
class PruneResult:
    """Counts deleted by one cleanup run."""

    turn_count: int
    keyword_count: int


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _batch_size(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > DEFAULT_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"batch size must not exceed {DEFAULT_BATCH_SIZE}"
        )
    return parsed


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _require_tables(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    existing = {str(row["name"]) for row in rows}
    missing = [name for name in _REQUIRED_TABLES if name not in existing]
    if missing:
        raise RuntimeError(
            f"Database is missing required table(s): {', '.join(missing)}"
        )


def inspect_prune_plan(connection: sqlite3.Connection, cutoff: datetime) -> PrunePlan:
    """Return the observation rows that are older than ``cutoff``."""
    cutoff_iso = cutoff.isoformat()
    row = connection.execute(
        "SELECT "
        "COUNT(*) AS turn_count, "
        "COALESCE(SUM(LENGTH(content)), 0) AS content_chars, "
        "COALESCE(SUM(LENGTH(metadata_json)), 0) AS metadata_chars, "
        "MIN(created_at) AS oldest_created_at, "
        "MAX(created_at) AS newest_created_at "
        "FROM memory_turns "
        "WHERE source = ? AND created_at < ?",
        (OBSERVATION_SOURCE, cutoff_iso),
    ).fetchone()
    if row is None:
        raise RuntimeError("Could not inspect memory_turns")
    return PrunePlan(
        cutoff=cutoff,
        turn_count=int(row["turn_count"]),
        content_chars=int(row["content_chars"]),
        metadata_chars=int(row["metadata_chars"]),
        oldest_created_at=(
            str(row["oldest_created_at"])
            if row["oldest_created_at"] is not None
            else None
        ),
        newest_created_at=(
            str(row["newest_created_at"])
            if row["newest_created_at"] is not None
            else None
        ),
    )


def ensure_cleanup_indexes(connection: sqlite3.Connection) -> None:
    """Create indexes needed for bounded, repeatable cleanup batches."""
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_turns_source_created "
        "ON memory_turns(source, created_at, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_keywords_turn_id "
        "ON memory_keywords(turn_id)"
    )
    connection.commit()


def prune_observations(
    connection: sqlite3.Connection,
    *,
    cutoff: datetime,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> PruneResult:
    """Delete expired observations and their keyword rows in short transactions."""
    if batch_size < 1 or batch_size > DEFAULT_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {DEFAULT_BATCH_SIZE}")

    cutoff_iso = cutoff.isoformat()
    deleted_turns = 0
    deleted_keywords = 0

    while True:
        rows = connection.execute(
            "SELECT id FROM memory_turns "
            "WHERE source = ? AND created_at < ? "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (OBSERVATION_SOURCE, cutoff_iso, batch_size),
        ).fetchall()
        if not rows:
            break

        turn_ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in turn_ids)
        with connection:
            keyword_cursor = connection.execute(
                f"DELETE FROM memory_keywords WHERE turn_id IN ({placeholders})",
                turn_ids,
            )
            turn_cursor = connection.execute(
                f"DELETE FROM memory_turns WHERE id IN ({placeholders})",
                turn_ids,
            )
        deleted_keywords += keyword_cursor.rowcount
        deleted_turns += turn_cursor.rowcount

    return PruneResult(turn_count=deleted_turns, keyword_count=deleted_keywords)


def _print_plan(plan: PrunePlan, *, applying: bool) -> None:
    label = "[APPLY]" if applying else "[DRY RUN]"
    print(f"{label} Source: {OBSERVATION_SOURCE}")
    print(f"{label} Cutoff: {plan.cutoff.isoformat()}")
    print(f"{label} Eligible observation turns: {plan.turn_count}")
    print(
        f"{label} Estimated stored text: "
        f"{plan.content_chars} content chars, {plan.metadata_chars} metadata chars"
    )
    if plan.oldest_created_at is not None:
        print(
            f"{label} Eligible time range: "
            f"{plan.oldest_created_at} to {plan.newest_created_at}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prune expired observed-only group messages from nahida.db"
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to nahida.db")
    parser.add_argument(
        "--older-than-days",
        required=True,
        type=_positive_int,
        help="Delete observations older than this many days",
    )
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows deleted per transaction (default: {DEFAULT_BATCH_SIZE}, max: {DEFAULT_BATCH_SIZE})",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Perform the deletion (the default is a dry run)",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly request the default read-only preview",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after deletion; stop the bot before using this option",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.vacuum and not args.apply:
        parser.error("--vacuum requires --apply")
    if not args.db.is_file():
        parser.error(f"database not found: {args.db}")

    cutoff = datetime.now(UTC) - timedelta(days=args.older_than_days)
    connection = _connect(args.db)
    try:
        _require_tables(connection)
        plan = inspect_prune_plan(connection, cutoff)
        _print_plan(plan, applying=args.apply)

        if not args.apply:
            print("[DRY RUN] No rows were deleted. Re-run with --apply to delete them.")
            return

        print("[APPLY] Ensuring cleanup indexes...")
        ensure_cleanup_indexes(connection)
        result = prune_observations(
            connection,
            cutoff=cutoff,
            batch_size=args.batch_size,
        )
        print(
            f"[APPLY] Deleted {result.turn_count} observation turns and "
            f"{result.keyword_count} keyword rows."
        )
        if args.vacuum:
            print(
                "[APPLY] Running WAL checkpoint and VACUUM; the bot should be stopped."
            )
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            connection.execute("VACUUM")
            print("[APPLY] VACUUM complete.")
    except (RuntimeError, sqlite3.Error) as exc:
        print(f"Cleanup failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    finally:
        connection.close()


if __name__ == "__main__":
    main()
