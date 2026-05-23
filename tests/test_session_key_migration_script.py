"""Tests for the one-shot legacy session-key migration script."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from scripts import migrate_session_keys as migrate

NOW = "2026-05-22T00:00:00+00:00"


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "nahida.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                workspace_id TEXT,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL,
                metadata_json TEXT
            );

            CREATE TABLE memory_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE TABLE active_sessions (
                chat_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE cron_jobs (
                job_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                session_key TEXT NOT NULL,
                prompt TEXT NOT NULL,
                mode TEXT NOT NULL,
                fire_at TEXT,
                interval_seconds INTEGER,
                max_runs INTEGER,
                run_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                next_fire_at TEXT NOT NULL,
                last_fired_at TEXT,
                workspace_id TEXT,
                claimed_at TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                cron_expression TEXT,
                session_mode TEXT NOT NULL DEFAULT 'main',
                chat_type TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE background_tasks (
                task_id TEXT PRIMARY KEY,
                runtime TEXT NOT NULL,
                status TEXT NOT NULL,
                requester_session_id TEXT NOT NULL,
                child_session_id TEXT,
                parent_task_id TEXT,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                delivery_target_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ended_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            );

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
            );

            CREATE TABLE memory_item_fts (
                item_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                title_index TEXT,
                content_index TEXT
            );

            CREATE TABLE memory_candidates (
                candidate_id TEXT PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                confidence REAL NOT NULL DEFAULT 0.5,
                evidence_json TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
    return db_path


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, workspace_id, created_at, last_active_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "default",
            NOW,
            NOW,
            json.dumps(metadata) if metadata is not None else None,
        ),
    )


def _insert_turn(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    chat_type: str | None = None,
) -> int:
    metadata = None
    if chat_type is not None:
        metadata = {
            "message_context": {
                "channel": "milky",
                "chat_type": chat_type,
                "chat_id": "10001",
            }
        }
    cursor = conn.execute(
        """
        INSERT INTO memory_turns (
            session_id, role, content, source, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "user",
            "hello",
            "test",
            json.dumps(metadata) if metadata is not None else None,
            NOW,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_cron(
    conn: sqlite3.Connection,
    *,
    job_id: str = "job-1",
    session_key: str = "milky:10001",
    chat_type: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO cron_jobs (
            job_id, platform, chat_id, session_key, prompt, mode,
            run_count, is_active, created_at, next_fire_at, session_mode, chat_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "milky",
            "10001",
            session_key,
            "ping",
            "ask",
            0,
            1,
            NOW,
            NOW,
            "main",
            chat_type,
        ),
    )


def _entry(plan: migrate.MigrationPlan, session_id: str) -> migrate.PlanEntry:
    for item in plan.entries:
        if item.old_session_id == session_id:
            return item
    raise AssertionError(f"missing plan entry for {session_id}")


def _write_plan(tmp_path: Path, plan: migrate.MigrationPlan) -> Path:
    plan_path = tmp_path / "migration-plan.json"
    migrate.write_plan(plan, plan_path)
    return plan_path


def _fetch_one(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row


def _fetch_value(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> Any:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


def test_inspect_suggests_rename_from_turn_metadata(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")
        _insert_turn(conn, "milky:10001", chat_type="group")

    plan = migrate.inspect_database(db_path)
    entry = _entry(plan, "milky:10001")

    assert entry.recommendation == "rename"
    assert entry.new_session_id == "milky:group:10001"
    assert entry.confidence == "high"
    assert entry.affected.memory_turns == 1
    assert [
        evidence
        for evidence in entry.evidence
        if evidence.source == "memory_turns.metadata_json.message_context"
        and evidence.chat_address == "milky:group:10001"
        and evidence.turn_count == 1
    ]


def test_inspect_suggests_keep_legacy_when_no_evidence(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")

    entry = _entry(migrate.inspect_database(db_path), "milky:10001")

    assert entry.recommendation == "keep_legacy"
    assert entry.new_session_id is None
    assert entry.confidence == "low"


def test_inspect_marks_invalid_session_for_manual_review(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "not-a-session-key")

    entry = _entry(migrate.inspect_database(db_path), "not-a-session-key")

    assert entry.recommendation == "manual_review"
    assert entry.confidence == "low"


def test_inspect_suggests_disable_cron_when_legacy_cron_has_no_type(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")
        _insert_cron(conn)

    entry = _entry(migrate.inspect_database(db_path), "milky:10001")

    assert entry.recommendation == "disable_cron"
    assert entry.affected.cron_jobs == 1


def test_inspect_suggests_split_for_conflicting_turn_addresses(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")
        private_turn_id = _insert_turn(conn, "milky:10001", chat_type="private")
        group_turn_id = _insert_turn(conn, "milky:10001", chat_type="group")

    entry = _entry(migrate.inspect_database(db_path), "milky:10001")
    split_targets = {target.chat_address: target for target in entry.split_targets}

    assert entry.recommendation == "split"
    assert entry.confidence == "conflict"
    assert split_targets["milky:private:10001"].turn_ids == [private_turn_id]
    assert split_targets["milky:group:10001"].turn_ids == [group_turn_id]


def test_apply_approved_rename_updates_references_and_logs(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001", metadata={"source": "legacy"})
        _insert_turn(conn, "milky:10001", chat_type="group")
        conn.execute(
            "INSERT INTO active_sessions VALUES (?, ?, ?)",
            ("milky:10001", "milky:10001:abcd1234", NOW),
        )
        _insert_cron(conn, chat_type="group")
        conn.execute(
            """
            INSERT INTO background_tasks (
                task_id, runtime, status, requester_session_id, child_session_id,
                title, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-1",
                "local",
                "running",
                "milky:10001",
                "milky:10001:subagent:t1",
                "Task",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO memory_items (
                item_id, scope_type, scope_id, kind, content, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("item-1", "session", "milky:10001", "fact", "fact", NOW, NOW),
        )
        conn.execute(
            "INSERT INTO memory_item_fts VALUES (?, ?, ?, ?, ?)",
            ("item-1", "session", "milky:10001", "fact", "fact"),
        )
        conn.execute(
            """
            INSERT INTO memory_candidates (
                candidate_id, scope_type, scope_id, kind, content, created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("cand-1", "session", "milky:10001", "fact", "candidate", NOW, NOW),
        )

    plan = migrate.inspect_database(db_path)
    entry = _entry(plan, "milky:10001")
    entry.approval = "approved"
    result = migrate.apply_plan(
        db_path=db_path,
        plan_path=_write_plan(tmp_path, plan),
        backup=False,
    )

    assert result == {"renamed": 1}
    assert (
        _fetch_value(
            db_path,
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            ("milky:10001",),
        )
        == 0
    )
    new_session = _fetch_one(
        db_path,
        "SELECT metadata_json FROM sessions WHERE session_id = ?",
        ("milky:group:10001",),
    )
    metadata = json.loads(new_session["metadata_json"])
    assert metadata["chat_address"]["target_type"] == "group"
    assert _fetch_value(db_path, "SELECT session_id FROM memory_turns") == (
        "milky:group:10001"
    )
    active = _fetch_one(db_path, "SELECT * FROM active_sessions")
    assert active["chat_key"] == "milky:group:10001"
    assert active["session_id"] == "milky:group:10001:abcd1234"
    cron = _fetch_one(db_path, "SELECT session_key, chat_type FROM cron_jobs")
    assert dict(cron) == {"session_key": "milky:group:10001", "chat_type": "group"}
    task = _fetch_one(
        db_path,
        "SELECT requester_session_id, child_session_id FROM background_tasks",
    )
    assert task["requester_session_id"] == "milky:group:10001"
    assert task["child_session_id"] == "milky:group:10001:subagent:t1"
    assert _fetch_value(db_path, "SELECT scope_id FROM memory_items") == (
        "milky:group:10001"
    )
    assert _fetch_value(db_path, "SELECT scope_id FROM memory_item_fts") == (
        "milky:group:10001"
    )
    assert _fetch_value(db_path, "SELECT scope_id FROM memory_candidates") == (
        "milky:group:10001"
    )
    log = _fetch_one(
        db_path,
        "SELECT old_session_id, new_session_id, status FROM session_key_migration_log",
    )
    assert dict(log) == {
        "old_session_id": "milky:10001",
        "new_session_id": "milky:group:10001",
        "status": "renamed",
    }


def test_apply_dry_run_changes_nothing(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")
        _insert_turn(conn, "milky:10001", chat_type="group")

    plan = migrate.inspect_database(db_path)
    _entry(plan, "milky:10001").approval = "approved"

    result = migrate.apply_plan(
        db_path=db_path,
        plan_path=_write_plan(tmp_path, plan),
        dry_run=True,
        backup=False,
    )

    assert result == {"renamed": 1}
    assert (
        _fetch_value(
            db_path,
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            ("milky:10001",),
        )
        == 1
    )
    assert (
        _fetch_value(
            db_path,
            "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
            ("milky:group:10001",),
        )
        == 0
    )
    assert _fetch_value(db_path, "SELECT COUNT(*) FROM session_key_migration_log") == 0


def test_force_keep_legacy_marks_metadata_and_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")

    plan = migrate.inspect_database(db_path)
    _entry(plan, "milky:10001").approval = "force_keep_legacy"
    plan_path = _write_plan(tmp_path, plan)

    assert migrate.apply_plan(db_path=db_path, plan_path=plan_path, backup=False) == {
        "kept_legacy": 1
    }
    assert migrate.apply_plan(db_path=db_path, plan_path=plan_path, backup=False) == {
        "already_applied": 1
    }
    session = _fetch_one(
        db_path,
        "SELECT metadata_json FROM sessions WHERE session_id = ?",
        ("milky:10001",),
    )
    assert json.loads(session["metadata_json"])["legacy_untyped"] is True
    assert (
        _fetch_value(
            db_path,
            "SELECT status FROM session_key_migration_log WHERE old_session_id = ?",
            ("milky:10001",),
        )
        == "kept_legacy"
    )


def test_apply_disable_cron_deactivates_legacy_job(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")
        _insert_cron(conn)

    plan = migrate.inspect_database(db_path)
    _entry(plan, "milky:10001").approval = "approved"

    assert migrate.apply_plan(
        db_path=db_path,
        plan_path=_write_plan(tmp_path, plan),
        backup=False,
    ) == {"cron_disabled": 1}
    cron = _fetch_one(db_path, "SELECT is_active, last_error FROM cron_jobs")
    assert cron["is_active"] == 0
    assert "target type is unknown" in cron["last_error"]
    assert _fetch_value(db_path, "SELECT status FROM session_key_migration_log") == (
        "cron_disabled"
    )


def test_force_split_moves_explicit_turns_and_keeps_legacy_shell(
    tmp_path: Path,
) -> None:
    db_path = _make_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        _insert_session(conn, "milky:10001")
        private_turn_id = _insert_turn(conn, "milky:10001", chat_type="private")
        group_turn_id = _insert_turn(conn, "milky:10001", chat_type="group")

    plan = migrate.inspect_database(db_path)
    _entry(plan, "milky:10001").approval = "force_split"

    assert migrate.apply_plan(
        db_path=db_path,
        plan_path=_write_plan(tmp_path, plan),
        backup=False,
    ) == {"split": 1}
    assert (
        _fetch_value(
            db_path,
            "SELECT session_id FROM memory_turns WHERE id = ?",
            (private_turn_id,),
        )
        == "milky:private:10001"
    )
    assert (
        _fetch_value(
            db_path,
            "SELECT session_id FROM memory_turns WHERE id = ?",
            (group_turn_id,),
        )
        == "milky:group:10001"
    )
    legacy = _fetch_one(
        db_path,
        "SELECT metadata_json FROM sessions WHERE session_id = ?",
        ("milky:10001",),
    )
    assert json.loads(legacy["metadata_json"])["legacy_untyped"] is True
    assert (
        _fetch_value(db_path, "SELECT status FROM session_key_migration_log") == "split"
    )
