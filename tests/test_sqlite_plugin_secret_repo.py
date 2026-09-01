from __future__ import annotations

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_plugin_secret_repo import (
    SQLitePluginSecretRepository,
)


@pytest.mark.asyncio
async def test_plugin_secret_roundtrip_update_delete_and_isolation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = DatabaseEngine(tmp_path / "plugin-secrets.db")
    await engine.initialize()
    try:
        repo = SQLitePluginSecretRepository(engine)
        await repo.set("plugin-a", "account", "secret-one")
        assert await repo.get("plugin-a", "account") == "secret-one"
        assert await repo.get("plugin-b", "account") is None

        await repo.set("plugin-a", "account", "secret-two")
        assert await repo.get("plugin-a", "account") == "secret-two"
        assert await repo.delete("plugin-a", "account") is True
        assert await repo.get("plugin-a", "account") is None
        assert await repo.delete("plugin-a", "account") is False
    finally:
        await engine.close()
