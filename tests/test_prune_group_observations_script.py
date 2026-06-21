from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.prune_group_observations import (
    ensure_cleanup_indexes,
    inspect_prune_plan,
    prune_observations,
)


def _create_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE memory_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            metadata_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE memory_keywords (
            turn_id INTEGER NOT NULL,
            keyword TEXT NOT NULL
        )
        """
    )
    return connection


def _insert_turn(
    connection: sqlite3.Connection,
    *,
    content: str,
    source: str,
    created_at: datetime,
) -> int:
    cursor = connection.execute(
        "INSERT INTO memory_turns "
        "(session_id, role, content, source, metadata_json, created_at) "
        "VALUES ('milky:group:42', 'user', ?, ?, '{}', ?)",
        (content, source, created_at.isoformat()),
    )
    turn_id = int(cursor.lastrowid)
    connection.execute(
        "INSERT INTO memory_keywords (turn_id, keyword) VALUES (?, 'message')",
        (turn_id,),
    )
    return turn_id


def test_inspect_prune_plan_only_counts_expired_observations(tmp_path: Path) -> None:
    connection = _create_db(tmp_path / "nahida.db")
    now = datetime.now(UTC)
    try:
        _insert_turn(
            connection,
            content="expired observation",
            source="group_observation",
            created_at=now - timedelta(days=8),
        )
        _insert_turn(
            connection,
            content="recent observation",
            source="group_observation",
            created_at=now - timedelta(hours=1),
        )
        _insert_turn(
            connection,
            content="expired normal turn",
            source="user_input",
            created_at=now - timedelta(days=8),
        )
        connection.commit()

        plan = inspect_prune_plan(connection, now - timedelta(days=7))

        assert plan.turn_count == 1
        assert plan.content_chars == len("expired observation")
    finally:
        connection.close()


def test_prune_removes_only_expired_observations_and_keywords(tmp_path: Path) -> None:
    connection = _create_db(tmp_path / "nahida.db")
    now = datetime.now(UTC)
    try:
        expired_one = _insert_turn(
            connection,
            content="expired observation one",
            source="group_observation",
            created_at=now - timedelta(days=8),
        )
        expired_two = _insert_turn(
            connection,
            content="expired observation two",
            source="group_observation",
            created_at=now - timedelta(days=9),
        )
        recent_observation = _insert_turn(
            connection,
            content="recent observation",
            source="group_observation",
            created_at=now - timedelta(hours=1),
        )
        normal_turn = _insert_turn(
            connection,
            content="expired normal turn",
            source="user_input",
            created_at=now - timedelta(days=8),
        )
        connection.commit()

        ensure_cleanup_indexes(connection)
        result = prune_observations(
            connection,
            cutoff=now - timedelta(days=7),
            batch_size=1,
        )

        assert result.turn_count == 2
        assert result.keyword_count == 2
        turn_ids = {
            int(row["id"])
            for row in connection.execute("SELECT id FROM memory_turns").fetchall()
        }
        keyword_turn_ids = {
            int(row["turn_id"])
            for row in connection.execute(
                "SELECT turn_id FROM memory_keywords"
            ).fetchall()
        }
        index_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

        assert turn_ids == {recent_observation, normal_turn}
        assert keyword_turn_ids == {recent_observation, normal_turn}
        assert expired_one not in turn_ids
        assert expired_two not in turn_ids
        assert "idx_memory_turns_source_created" in index_names
        assert "idx_memory_keywords_turn_id" in index_names
    finally:
        connection.close()
