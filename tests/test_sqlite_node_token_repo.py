from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.db.repositories.sqlite_node_token_repo import SQLiteNodeTokenStore
from nahida_bot.gateway.services.node_auth import NodeAuthService


async def _service(db_path) -> tuple[DatabaseEngine, NodeAuthService]:  # type: ignore[no-untyped-def]
    engine = DatabaseEngine(db_path)
    await engine.initialize()
    return engine, NodeAuthService(store=SQLiteNodeTokenStore(engine))


@pytest.mark.asyncio
async def test_node_token_survives_engine_and_service_recreation(tmp_path) -> None:
    db_path = tmp_path / "gateway.db"
    engine, service = await _service(db_path)
    full_token, token_id = await service.issue_node_token(
        node_id="desktop-1",
        display_name="Desktop",
        scope=("live2d", "speech"),
        actor_account_key="desktop:user:owner",
        conversation_id="conversation:private:desktop",
    )
    await engine.close()

    engine, rebuilt = await _service(db_path)
    try:
        principal = await rebuilt.verify(full_token)
        assert principal is not None
        assert principal.token_id == token_id
        assert principal.scope == ("live2d", "speech")
        assert principal.actor_account_key == "desktop:user:owner"
        assert principal.conversation_id == "conversation:private:desktop"

        record = await rebuilt.store.get(token_id)
        assert record is not None
        assert record.display_name == "Desktop"
        assert record.token_digest != full_token
    finally:
        await engine.close()

    with sqlite3.connect(db_path) as connection:
        stored = connection.execute(
            "SELECT token_digest FROM node_tokens WHERE token_id = ?", (token_id,)
        ).fetchone()
    assert stored is not None
    assert stored[0] != full_token
    assert full_token not in stored[0]


@pytest.mark.asyncio
async def test_pairing_use_and_revocation_survive_service_recreation(tmp_path) -> None:
    db_path = tmp_path / "gateway.db"
    engine, first = await _service(db_path)
    pairing_token, pairing_id = await first.issue_pairing_token(node_id="desktop-1")
    issued = await first.exchange_pairing_for_node_token(pairing_token)
    assert issued is not None
    node_token, node_token_id = issued
    assert await first.revoke(node_token_id)
    await engine.close()

    engine, rebuilt = await _service(db_path)
    try:
        assert await rebuilt.exchange_pairing_for_node_token(pairing_token) is None
        assert await rebuilt.verify(node_token) is None
        pairing_record = await rebuilt.store.get(pairing_id)
        node_record = await rebuilt.store.get(node_token_id)
        assert pairing_record is not None and pairing_record.used
        assert node_record is not None and node_record.revoked
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_pairing_token_is_atomically_consumed_across_services(tmp_path) -> None:
    engine, first = await _service(tmp_path / "gateway.db")
    second = NodeAuthService(store=SQLiteNodeTokenStore(engine))
    pairing_token, _ = await first.issue_pairing_token(node_id="desktop-1")
    try:
        results = await asyncio.gather(
            first.exchange_pairing_for_node_token(pairing_token),
            second.exchange_pairing_for_node_token(pairing_token),
        )
        assert sum(result is not None for result in results) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_persisted_expiry_is_enforced_after_recreation(tmp_path) -> None:
    db_path = tmp_path / "gateway.db"
    engine, first = await _service(db_path)
    full_token, token_id = await first.issue_node_token(
        node_id="desktop-1", ttl_seconds=60
    )
    async with engine.write_lock:
        await engine.execute(
            "UPDATE node_tokens SET expires_at = ? WHERE token_id = ?",
            (time.time() - 1, token_id),
        )
        await engine.db.commit()
    await engine.close()

    engine, rebuilt = await _service(db_path)
    try:
        assert await rebuilt.verify(full_token) is None
    finally:
        await engine.close()
