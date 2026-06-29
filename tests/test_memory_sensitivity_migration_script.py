from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.migrate_memory_sensitivity import (
    apply_plan,
    inspect_database,
    write_plan,
)


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE memory_items (
                item_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                confidence REAL NOT NULL DEFAULT 1.0,
                importance REAL NOT NULL DEFAULT 0.5,
                sensitivity TEXT NOT NULL DEFAULT 'private',
                sensitivity_source TEXT NOT NULL DEFAULT 'default',
                source TEXT NOT NULL DEFAULT 'plugin',
                evidence_json TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_item(
    path: Path,
    *,
    item_id: str,
    kind: str = "fact",
    sensitivity: str = "private",
    sensitivity_source: str = "default",
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO memory_items
            (item_id, scope_type, scope_id, kind, title, content, status,
             confidence, importance, sensitivity, sensitivity_source, source,
             evidence_json, metadata_json, created_at, updated_at)
            VALUES (?, 'chat', 'chatA', ?, '', ?, 'active', 1.0, 0.5, ?, ?,
                    'consolidation', NULL, NULL,
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (item_id, kind, f"{item_id} content", sensitivity, sensitivity_source),
        )
        conn.commit()
    finally:
        conn.close()


def test_inspect_proposes_flip_only_for_default_private(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="m_default_priv",
        sensitivity="private",
        sensitivity_source="default",
    )
    _insert_item(
        db_path,
        item_id="m_explicit_priv",
        sensitivity="private",
        sensitivity_source="explicit",
    )
    _insert_item(
        db_path,
        item_id="m_dream_secret",
        sensitivity="secret_like",
        sensitivity_source="dream",
    )
    _insert_item(
        db_path, item_id="m_public", sensitivity="public", sensitivity_source="default"
    )

    plan = inspect_database(db_path=db_path)
    by_id = {e.item_id: e for e in plan.entries}

    assert by_id["m_default_priv"].action == "flip_to_public"
    # Explicitly/auto-tagged restrictions must NOT be softened.
    assert by_id["m_explicit_priv"].action == "keep"
    assert by_id["m_dream_secret"].action == "keep"
    assert by_id["m_public"].action == "keep"
    assert plan.summary["flip_to_public"] == 1
    assert plan.summary["keep"] == 3


def test_apply_flips_default_private_and_leaves_rest(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    plan_path = tmp_path / "plan.json"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="m_default_priv",
        sensitivity="private",
        sensitivity_source="default",
    )
    _insert_item(
        db_path,
        item_id="m_explicit_priv",
        sensitivity="private",
        sensitivity_source="explicit",
    )

    plan = inspect_database(db_path=db_path)
    for entry in plan.entries:
        if entry.action == "flip_to_public":
            entry.approval = "approved"
    write_plan(plan, plan_path)

    result = apply_plan(db_path=db_path, plan_path=plan_path, confirm=True)

    assert result["flipped"] == 1
    backups = list(tmp_path.glob("memory.sqlite3.memory-sensitivity-*.bak"))
    assert len(backups) == 1

    conn = sqlite3.connect(db_path)
    try:
        rows = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT item_id, sensitivity, sensitivity_source FROM memory_items"
            )
        }
        log_count = conn.execute(
            "SELECT COUNT(*) FROM memory_sensitivity_migration_log"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows["m_default_priv"] == ("public", "default")
    assert rows["m_explicit_priv"] == ("private", "explicit")
    assert log_count == 1


def test_apply_dry_run_rolls_back(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    plan_path = tmp_path / "plan.json"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="m_default_priv",
        sensitivity="private",
        sensitivity_source="default",
    )

    plan = inspect_database(db_path=db_path)
    for entry in plan.entries:
        if entry.action == "flip_to_public":
            entry.approval = "approved"
    write_plan(plan, plan_path)

    result = apply_plan(db_path=db_path, plan_path=plan_path, dry_run=True)
    assert result["flipped"] == 1

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sensitivity FROM memory_items WHERE item_id = 'm_default_priv'"
        ).fetchone()
        log_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='memory_sensitivity_migration_log'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "private"  # unchanged
    assert log_table is None  # dry-run rolled the log table back too


def test_apply_all_safe_flips_without_per_entry_approval(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    plan_path = tmp_path / "plan.json"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="m_default_priv",
        sensitivity="private",
        sensitivity_source="default",
    )

    plan = inspect_database(db_path=db_path)
    write_plan(plan, plan_path)  # entries stay pending

    result = apply_plan(
        db_path=db_path, plan_path=plan_path, confirm=True, apply_all_safe=True
    )
    assert result["flipped"] == 1
