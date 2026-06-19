"""SQLite repository for person/account identity data (issue #7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.identity.models import AccountKey


def _utc_now_iso() -> str:
    """Return the current UTC time as an aware ISO8601 string."""
    return datetime.now(UTC).isoformat()


class SQLiteIdentityRepository:
    """Typed SQLite data access for persons, account links, and observations."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    # ── Persons ─────────────────────────────────────────────

    async def upsert_person(
        self,
        *,
        person_id: str,
        display_name: str = "",
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a person row, refreshing ``updated_at``."""
        now_iso = _utc_now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO persons (person_id, display_name, status, "
                "metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(person_id) DO UPDATE SET "
                "display_name = excluded.display_name, "
                "status = excluded.status, "
                "metadata_json = excluded.metadata_json, "
                "updated_at = excluded.updated_at",
                (person_id, display_name, status, metadata_json, now_iso, now_iso),
            )
            await self._engine.db.commit()

    # ── Account links ──────────────────────────────────────

    async def upsert_account_link(
        self,
        *,
        account_key: str,
        person_id: str,
        label: str = "",
        status: str = "active",
        verification: str = "manual_link",
        linked_by: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Bind an account to a person, creating the person if missing.

        ``channel`` / ``account_type`` / ``platform_account_id`` are derived
        from ``account_key`` (the canonical source) so the unique-active index
        on ``(channel, account_type, platform_account_id)`` can never diverge
        from the ``account_key`` primary key. ``account_type`` is always
        ``"user"`` — the only segment in the ``{channel}:user:{id}`` key format.
        """
        parsed = AccountKey.parse(account_key)
        channel = parsed.channel
        account_type = "user"
        platform_account_id = parsed.platform_user_id
        now_iso = _utc_now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO person_accounts (account_key, person_id, channel, "
                "account_type, platform_account_id, label, status, verification, "
                "linked_by, linked_at, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_key) DO UPDATE SET "
                "person_id = excluded.person_id, "
                "channel = excluded.channel, "
                "account_type = excluded.account_type, "
                "platform_account_id = excluded.platform_account_id, "
                "label = excluded.label, "
                "status = excluded.status, "
                "verification = excluded.verification, "
                "linked_by = excluded.linked_by, "
                "linked_at = excluded.linked_at, "
                "metadata_json = excluded.metadata_json",
                (
                    account_key,
                    person_id,
                    channel,
                    account_type,
                    platform_account_id,
                    label,
                    status,
                    verification,
                    linked_by,
                    now_iso,
                    metadata_json,
                ),
            )
            await self._engine.db.commit()

    async def resolve_account(
        self,
        account_key: str,
    ) -> tuple[str | None, str]:
        """Return ``(person_id, verification_source)`` for an active link.

        ``(None, "none")`` when the account has no active person binding.
        """
        rows = await self._engine.fetch_all(
            "SELECT person_id, verification FROM person_accounts "
            "WHERE account_key = ? AND status = 'active' LIMIT 1",
            (account_key,),
        )
        if not rows:
            return None, "none"
        row = rows[0]
        return str(row["person_id"]), str(row["verification"])

    async def list_accounts(self, person_id: str) -> list[dict[str, Any]]:
        """Return active account links for a person."""
        rows = await self._engine.fetch_all(
            "SELECT account_key, person_id, channel, account_type, "
            "platform_account_id, label, verification, linked_by, linked_at, "
            "metadata_json "
            "FROM person_accounts "
            "WHERE person_id = ? AND status = 'active' "
            "ORDER BY linked_at ASC",
            (person_id,),
        )
        return [
            {
                "account_key": str(row["account_key"]),
                "person_id": str(row["person_id"]),
                "channel": str(row["channel"]),
                "account_type": str(row["account_type"]),
                "platform_account_id": str(row["platform_account_id"]),
                "label": str(row["label"]),
                "verification": str(row["verification"]),
                "linked_by": str(row["linked_by"]),
                "linked_at": str(row["linked_at"]),
                "metadata": (
                    json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                ),
            }
            for row in rows
        ]

    async def unlink_account(self, account_key: str) -> bool:
        """Mark an account link inactive. Returns whether a row was affected."""
        now_iso = _utc_now_iso()
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "UPDATE person_accounts SET status = 'inactive', linked_at = ? "
                "WHERE account_key = ? AND status = 'active'",
                (now_iso, account_key),
            )
            await self._engine.db.commit()
        return cursor.rowcount > 0

    # ── Observations ───────────────────────────────────────

    async def record_observation(
        self,
        *,
        account_key: str,
        chat_address: str,
        display_name: str = "",
        role_tags: tuple[str, ...] = (),
        last_message_id: str = "",
    ) -> None:
        """Upsert how an account last appeared in a chat.

        On conflict ``(account_key, chat_address)`` refreshes ``last_seen_at``,
        ``last_message_id`` and ``display_name``; ``first_seen_at`` is kept.
        """
        now_iso = _utc_now_iso()
        role_tags_json = json.dumps(list(role_tags)) if role_tags else None
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO account_observations (account_key, chat_address, "
                "display_name, role_tags_json, first_seen_at, last_seen_at, "
                "last_message_id, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(account_key, chat_address) DO UPDATE SET "
                "display_name = excluded.display_name, "
                "role_tags_json = excluded.role_tags_json, "
                "last_seen_at = excluded.last_seen_at, "
                "last_message_id = excluded.last_message_id",
                (
                    account_key,
                    chat_address,
                    display_name,
                    role_tags_json,
                    now_iso,
                    now_iso,
                    last_message_id,
                ),
            )
            await self._engine.db.commit()

    async def count_observations(self, account_key: str) -> int:
        """Number of observation rows for an account (test/debug helper)."""
        rows = await self._engine.fetch_all(
            "SELECT COUNT(*) AS n FROM account_observations WHERE account_key = ?",
            (account_key,),
        )
        return int(rows[0]["n"]) if rows else 0
