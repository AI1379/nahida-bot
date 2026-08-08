"""SQLite repository for ChatGPT Codex OAuth tokens.

Stores per-provider refresh/access tokens so that ``CodexProvider`` can
authenticate without an API key in config. Refresh tokens are long-lived
secrets; access tokens are short-lived and refreshed in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from nahida_bot.db.engine import DatabaseEngine


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True, frozen=True)
class CodexToken:
    """A persisted ChatGPT OAuth token bundle.

    ``expires_at`` is Unix epoch seconds; ``0`` means unknown/expired.
    ``account_id`` is the ``chatgpt_account_id`` extracted from the JWT and
    sent as the ``ChatGPT-Account-Id`` header on every request.
    """

    refresh_token: str
    access_token: str
    expires_at: int
    account_id: str


class SQLiteCodexTokenRepository:
    """CRUD for the ``codex_tokens`` table, keyed by provider id."""

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def get(self, provider_id: str) -> CodexToken | None:
        row = await self._engine.fetch_one(
            "SELECT refresh_token, access_token, expires_at, account_id "
            "FROM codex_tokens WHERE provider_id = ?",
            (provider_id,),
        )
        if row is None:
            return None
        return CodexToken(
            refresh_token=str(row["refresh_token"]),
            access_token=str(row["access_token"]),
            expires_at=int(row["expires_at"]),
            account_id=str(row["account_id"] or ""),
        )

    async def upsert(self, provider_id: str, token: CodexToken) -> None:
        now = _utc_now_iso()
        async with self._engine.write_lock:
            await self._engine.execute(
                """
                INSERT INTO codex_tokens
                    (provider_id, refresh_token, access_token, expires_at,
                     account_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    refresh_token = excluded.refresh_token,
                    access_token  = excluded.access_token,
                    expires_at    = excluded.expires_at,
                    account_id    = excluded.account_id,
                    updated_at    = excluded.updated_at
                """,
                (
                    provider_id,
                    token.refresh_token,
                    token.access_token,
                    token.expires_at,
                    token.account_id,
                    now,
                    now,
                ),
            )
            await self._engine.db.commit()

    async def delete(self, provider_id: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM codex_tokens WHERE provider_id = ?",
                (provider_id,),
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def list_provider_ids(self) -> list[str]:
        rows = await self._engine.fetch_all(
            "SELECT provider_id FROM codex_tokens ORDER BY provider_id"
        )
        return [str(row["provider_id"]) for row in rows]
