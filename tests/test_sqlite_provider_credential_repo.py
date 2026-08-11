from __future__ import annotations

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_provider_credential_repo import (
    ProviderCredential,
    SQLiteProviderCredentialRepository,
    stored_provider_ids,
)


@pytest.mark.asyncio
async def test_provider_credential_roundtrip_and_delete(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "credentials.db"
    engine = DatabaseEngine(db_path)
    await engine.initialize()
    try:
        repo = SQLiteProviderCredentialRepository(engine)
        await repo.upsert(ProviderCredential("deepseek", "api_key", "secret-one"))
        fetched = await repo.get("deepseek")
        assert fetched is not None
        assert fetched.auth_method == "api_key"
        assert fetched.secret == "secret-one"
        assert stored_provider_ids(db_path) == frozenset({"deepseek"})

        await repo.upsert(ProviderCredential("deepseek", "api_key", "secret-two"))
        assert (await repo.get("deepseek")).secret == "secret-two"  # type: ignore[union-attr]
        assert await repo.delete("deepseek") is True
        assert await repo.get("deepseek") is None
        assert await repo.delete("deepseek") is False
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_list_all_never_changes_provider_order(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = DatabaseEngine(tmp_path / "credentials.db")
    await engine.initialize()
    try:
        repo = SQLiteProviderCredentialRepository(engine)
        await repo.upsert(ProviderCredential("zeta", "api_key", "z"))
        await repo.upsert(ProviderCredential("alpha", "api_key", "a"))
        assert [item.provider_id for item in await repo.list_all()] == [
            "alpha",
            "zeta",
        ]
    finally:
        await engine.close()
