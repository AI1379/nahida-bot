"""Identity memory migration planning tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.migrate_identity_memory import inspect_identity_memory


def test_inspect_uses_only_active_account_links(tmp_path: Path) -> None:
    db_path = tmp_path / "identity-memory.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE person_accounts (
          account_key TEXT, person_id TEXT, status TEXT
        );
        CREATE TABLE memory_items (
          item_id TEXT, kind TEXT, status TEXT, scope_type TEXT, scope_id TEXT,
          metadata_json TEXT, evidence_json TEXT, updated_at TEXT
        );
        INSERT INTO person_accounts VALUES ('desktop:user:owner', 'owner', 'active');
        INSERT INTO memory_items VALUES (
          'm1', 'preference', 'active', 'account', 'desktop:user:owner',
          '{}', '{}', '2026-01-01'
        );
        INSERT INTO memory_items VALUES (
          'm2', 'fact', 'active', 'chat', 'conversation:private:owner',
          '{"origin_account_key":"desktop:user:owner"}', '{}', '2026-01-02'
        );
        INSERT INTO memory_items VALUES (
          'm3', 'fact', 'active', 'chat', 'conversation:private:unknown',
          '{}', '{}', '2026-01-03'
        );
        """
    )
    conn.commit()
    conn.close()

    plan = inspect_identity_memory(db_path)
    by_id = {entry.item_id: entry for entry in plan.entries}

    assert by_id["m1"].action == "migrate"
    assert by_id["m1"].target_scope_id == "owner"
    assert by_id["m2"].action == "migrate"
    assert by_id["m2"].target_scope_type == "person"
    assert by_id["m3"].action == "manual_review"
