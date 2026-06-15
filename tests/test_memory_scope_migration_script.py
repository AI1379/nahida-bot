from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.migrate_memory_scope import apply_plan, inspect_database, write_plan


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
                source TEXT NOT NULL DEFAULT 'plugin',
                evidence_json TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE memory_item_fts USING fts5(
                item_id UNINDEXED,
                scope_type UNINDEXED,
                scope_id UNINDEXED,
                title_index,
                content_index
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
    kind: str,
    evidence: dict | None = None,
    metadata: dict | None = None,
    scope_type: str = "global",
    scope_id: str = "__global__",
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO memory_items
            (item_id, scope_type, scope_id, kind, title, content, status,
             confidence, importance, sensitivity, source, evidence_json,
             metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, '', ?, 'active', 1.0, 0.5, 'private',
                    'consolidation', ?, ?, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00')
            """,
            (
                item_id,
                scope_type,
                scope_id,
                kind,
                f"{item_id} content",
                json.dumps(evidence, ensure_ascii=False) if evidence else None,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )
        conn.execute(
            """
            INSERT INTO memory_item_fts
            (item_id, scope_type, scope_id, title_index, content_index)
            VALUES (?, ?, ?, '', ?)
            """,
            (item_id, scope_type, scope_id, f"{item_id} content"),
        )
        conn.commit()
    finally:
        conn.close()


def test_inspect_proposes_only_safe_chat_scoped_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="mem_pref",
        kind="preference",
        evidence={"session_id": "telegram:private:u1"},
    )
    _insert_item(
        db_path,
        item_id="mem_decision",
        kind="decision",
        evidence={"session_id": "telegram:private:u1"},
    )
    _insert_item(
        db_path,
        item_id="mem_legacy",
        kind="preference",
        evidence={"session_id": "telegram:u1"},
    )
    _insert_item(
        db_path,
        item_id="mem_context",
        kind="fact",
        metadata={
            "message_context": {
                "channel": "milky",
                "chat_type": "group",
                "chat_id": "g1",
            }
        },
    )

    plan = inspect_database(db_path=db_path)
    by_id = {entry.item_id: entry for entry in plan.entries}

    assert by_id["mem_pref"].action == "migrate"
    assert by_id["mem_pref"].target_scope_id == "telegram:private:u1"
    assert by_id["mem_decision"].action == "keep_global"
    assert by_id["mem_legacy"].action == "keep_global"
    assert by_id["mem_context"].action == "migrate"
    assert by_id["mem_context"].target_scope_id == "milky:group:g1"


def test_apply_plan_updates_memory_item_and_fts_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    plan_path = tmp_path / "plan.json"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="mem_pref",
        kind="preference",
        evidence={"session_id": "telegram:private:u1"},
    )

    plan = inspect_database(db_path=db_path)
    for entry in plan.entries:
        if entry.action == "migrate":
            entry.approval = "approved"
    write_plan(plan, plan_path)

    result = apply_plan(db_path=db_path, plan_path=plan_path, confirm=True)

    assert result["migrated"] == 1
    backups = list(tmp_path.glob("memory.sqlite3.memory-scope-*.bak"))
    conn = sqlite3.connect(db_path)
    try:
        item_row = conn.execute(
            "SELECT scope_type, scope_id FROM memory_items WHERE item_id = ?",
            ("mem_pref",),
        ).fetchone()
        fts_row = conn.execute(
            "SELECT scope_type, scope_id FROM memory_item_fts WHERE item_id = ?",
            ("mem_pref",),
        ).fetchone()
    finally:
        conn.close()
    assert len(backups) == 1
    assert item_row == ("chat", "telegram:private:u1")
    assert fts_row == ("chat", "telegram:private:u1")


def test_apply_plan_dry_run_rolls_back_scope_and_log_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    plan_path = tmp_path / "plan.json"
    _create_db(db_path)
    _insert_item(
        db_path,
        item_id="mem_pref",
        kind="preference",
        evidence={"session_id": "telegram:private:u1"},
    )

    plan = inspect_database(db_path=db_path)
    for entry in plan.entries:
        if entry.action == "migrate":
            entry.approval = "approved"
    write_plan(plan, plan_path)

    result = apply_plan(db_path=db_path, plan_path=plan_path, dry_run=True)

    assert result["migrated"] == 1
    conn = sqlite3.connect(db_path)
    try:
        item_row = conn.execute(
            "SELECT scope_type, scope_id FROM memory_items WHERE item_id = ?",
            ("mem_pref",),
        ).fetchone()
        fts_row = conn.execute(
            "SELECT scope_type, scope_id FROM memory_item_fts WHERE item_id = ?",
            ("mem_pref",),
        ).fetchone()
        log_table = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'memory_scope_migration_log'
            """
        ).fetchone()
    finally:
        conn.close()
    assert item_row == ("global", "__global__")
    assert fts_row == ("global", "__global__")
    assert log_table is None
