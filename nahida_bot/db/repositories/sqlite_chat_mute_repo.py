"""Persistent mute deadlines keyed by typed chat address, independent of history."""

from __future__ import annotations

from dataclasses import dataclass

from nahida_bot.db.engine import DatabaseEngine


@dataclass(frozen=True, slots=True)
class ChatMuteStatus:
    muted_until: float | None = None
    expired: bool = False

    @property
    def muted(self) -> bool:
        return self.muted_until is not None


class SQLiteChatMuteRepository:
    """Share the engine write lock so renewal, clearing and expiry stay atomic.

    Storage errors propagate to the command/event boundary. A failed mute
    check must never silently allow an agent run or claim a successful write.
    """

    def __init__(self, engine: DatabaseEngine) -> None:
        self._engine = engine

    async def set_until(
        self, chat_address: str, muted_until: float, actor: str, now: float
    ) -> None:
        async with self._engine.write_lock:
            await self._engine.execute(
                "INSERT INTO chat_mutes "
                "(chat_address, muted_until, actor_account_key, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_address) DO UPDATE SET "
                "muted_until=excluded.muted_until, "
                "actor_account_key=excluded.actor_account_key, "
                "updated_at=excluded.updated_at",
                (chat_address, muted_until, actor, now),
            )
            await self._engine.db.commit()

    async def clear(self, chat_address: str) -> bool:
        async with self._engine.write_lock:
            cursor = await self._engine.execute(
                "DELETE FROM chat_mutes WHERE chat_address=?", (chat_address,)
            )
            await self._engine.db.commit()
            return cursor.rowcount > 0

    async def status(self, chat_address: str, now: float) -> ChatMuteStatus:
        """Claim an expired deadline once, without deleting a concurrent renewal."""
        async with self._engine.write_lock:
            row = await self._engine.fetch_one(
                "SELECT muted_until FROM chat_mutes WHERE chat_address=?",
                (chat_address,),
            )
            if row is None:
                return ChatMuteStatus()
            deadline = float(row["muted_until"])
            if deadline > now:
                return ChatMuteStatus(muted_until=deadline)
            await self._engine.execute(
                "DELETE FROM chat_mutes WHERE chat_address=?", (chat_address,)
            )
            await self._engine.db.commit()
            return ChatMuteStatus(expired=True)
