from __future__ import annotations

import time

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_codex_token_repo import (
    CodexToken,
    SQLiteCodexTokenRepository,
)


@pytest.fixture
async def repo(tmp_path) -> SQLiteCodexTokenRepository:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "test_codex.db"
    engine = DatabaseEngine(str(db_path))
    await engine.initialize()
    try:
        yield SQLiteCodexTokenRepository(engine)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_get_returns_none_when_no_row(repo: SQLiteCodexTokenRepository) -> None:
    assert await repo.get("missing") is None


@pytest.mark.asyncio
async def test_upsert_then_get_roundtrips_token(
    repo: SQLiteCodexTokenRepository,
) -> None:
    token = CodexToken(
        refresh_token="r1",
        access_token="a1",
        expires_at=int(time.time()) + 3600,
        account_id="acct_1",
    )
    await repo.upsert("codex", token)

    fetched = await repo.get("codex")
    assert fetched is not None
    assert fetched.refresh_token == "r1"
    assert fetched.access_token == "a1"
    assert fetched.account_id == "acct_1"
    assert fetched.expires_at == token.expires_at


@pytest.mark.asyncio
async def test_upsert_replaces_existing_token_on_same_provider(
    repo: SQLiteCodexTokenRepository,
) -> None:
    original = CodexToken(
        refresh_token="r1",
        access_token="a1",
        expires_at=1000,
        account_id="acct_1",
    )
    refreshed = CodexToken(
        refresh_token="r2",
        access_token="a2",
        expires_at=2000,
        account_id="acct_1",
    )
    await repo.upsert("codex", original)
    await repo.upsert("codex", refreshed)

    fetched = await repo.get("codex")
    assert fetched is not None
    assert fetched.refresh_token == "r2"
    assert fetched.access_token == "a2"
    assert fetched.expires_at == 2000


@pytest.mark.asyncio
async def test_upsert_supports_multiple_providers_independently(
    repo: SQLiteCodexTokenRepository,
) -> None:
    await repo.upsert(
        "codex",
        CodexToken("r1", "a1", 1000, "x"),
    )
    await repo.upsert(
        "codex-alt",
        CodexToken("r2", "a2", 2000, "y"),
    )

    assert (await repo.get("codex")).refresh_token == "r1"  # type: ignore[union-attr]
    assert (await repo.get("codex-alt")).refresh_token == "r2"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_delete_removes_token_and_reports_rowcount(
    repo: SQLiteCodexTokenRepository,
) -> None:
    await repo.upsert("codex", CodexToken("r", "a", 1, "x"))

    assert await repo.delete("codex") is True
    assert await repo.get("codex") is None
    assert await repo.delete("codex") is False


@pytest.mark.asyncio
async def test_list_provider_ids_returns_sorted_ids(
    repo: SQLiteCodexTokenRepository,
) -> None:
    await repo.upsert("zeta", CodexToken("r", "a", 1, "x"))
    await repo.upsert("alpha", CodexToken("r", "a", 1, "x"))

    assert await repo.list_provider_ids() == ["alpha", "zeta"]


@pytest.mark.asyncio
async def test_account_id_defaults_to_empty_when_column_blank(
    repo: SQLiteCodexTokenRepository,
) -> None:
    await repo.upsert(
        "codex",
        CodexToken("r", "a", 1, ""),
    )
    fetched = await repo.get("codex")
    assert fetched is not None
    assert fetched.account_id == ""
