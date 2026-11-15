"""Operator views report full counts, real evidence and persisted session choices."""

from dataclasses import replace
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from nahida_bot.agent.memory.attribution import (
    EvidenceRef,
    MemoryAttribution,
    MemorySubject,
)
from nahida_bot.agent.memory.models import ConversationTurn
from nahida_bot.agent.memory.sqlite import SQLiteMemoryStore
from nahida_bot.db.engine import DatabaseEngine
from nahida_bot.gateway.app import WebAPIApp
from tests.test_webapi import _make_mock_app


@pytest.fixture
async def inspection_api(tmp_path):
    engine = DatabaseEngine(tmp_path / "memory.db")
    await engine.initialize()
    store = SQLiteMemoryStore(engine)
    app = _make_mock_app(auth_token="inspect-test")
    app.memory_store = store
    web = WebAPIApp(
        application=app, host="127.0.0.1", port=6185, auth_token="inspect-test"
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=web.fastapi_app), base_url="http://test"
        ) as client:
            yield client, store, engine
    finally:
        await engine.close()


AUTH = {"Authorization": "Bearer inspect-test"}


async def test_overview_counts_are_not_the_first_page(inspection_api):
    client, store, _ = inspection_api
    for number in range(155):
        await store.append_item(
            title=f"item {number}",
            content="legacy fact",
            scope_type="chat",
            scope_id="milky:group:1",
        )
    archived = await store.append_item(
        content="old fact", scope_type="chat", scope_id="milky:group:1"
    )
    await store.archive_item(archived)
    await store.append_item(
        content="private fact",
        scope_type="account",
        scope_id="milky:user:b",
        sensitivity="private",
    )
    response = await client.get("/api/memory/overview", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert (data["total"], data["active"], data["archived"], data["restricted"]) == (
        157,
        156,
        1,
        1,
    )
    assert data["without_subject"] == 156 and data["without_evidence"] == 156
    scoped = (
        await client.get(
            "/api/memory/overview?scope_type=chat&scope_id=milky:group:1", headers=AUTH
        )
    ).json()
    assert scoped["active"] == 155 and scoped["restricted"] == 0
    empty = (
        await client.get("/api/memory/overview?scope_id=missing", headers=AUTH)
    ).json()
    assert empty["total"] == 0 and empty["scopes"] == []


async def test_evidence_resolves_real_message_and_rejects_wrong_subject_source(
    inspection_api,
):
    client, store, _ = inspection_api
    session = "milky:group:1"
    await store.ensure_session(session)
    turn_id = await store.append_turn(
        session,
        ConversationTurn(
            "user",
            "Alice prefers tea.",
            metadata={
                "message_context": {
                    "sender_id": "a",
                    "sender_display_name": "Alice",
                    "message_id": "message-1",
                },
            },
        ),
    )
    record = (await store.get_turns_by_ids([turn_id]))[0]
    ref = EvidenceRef.from_record(record)
    item_id = await store.append_item(
        content="Prefers tea",
        scope_type="chat",
        scope_id=session,
        metadata={
            "portable": False,
            "attribution": MemoryAttribution(
                MemorySubject("account", "milky:user:a"),
                (
                    ref,
                    replace(ref, turn_id=0),
                    replace(ref, account_key="milky:user:b"),
                    replace(ref, turn_id=999999),
                ),
            ).to_dict(),
        },
    )
    await store.archive_item(item_id)
    response = await client.get(f"/api/memory/items/{item_id}", headers=AUTH)
    assert response.status_code == 200
    detail = response.json()
    assert detail["item"]["status"] == "archived"
    assert detail["subject"]["id"] == "milky:user:a" and detail["portable"] is False
    assert [e["status"] for e in detail["evidence"]] == [
        "available",
        "available",
        "mismatch",
        "missing",
    ]
    assert detail["evidence"][0]["turn"]["content"] == "Alice prefers tea."
    assert detail["evidence"][2]["turn"] is None


async def test_session_policy_persists_without_clobbering_other_runtime_fields(
    inspection_api,
):
    client, store, _ = inspection_api
    session = "milky:group:1"
    await store.ensure_session(session)
    await store.update_session_meta(
        session,
        {"model": "existing-model", "runtime": {"reasoning": {"effort": "high"}}},
    )
    path = f"/api/sessions/{session}/error-notifications"
    assert (await client.get(path, headers=AUTH)).json() == {"mode": "interactive"}
    response = await client.put(path, headers=AUTH, json={"mode": "none"})
    assert response.status_code == 200
    assert (await client.get(path, headers=AUTH)).json() == {"mode": "none"}
    assert (await store.get_session_meta(session)) == {
        "model": "existing-model",
        "runtime": {"reasoning": {"effort": "high"}, "error_notifications": "none"},
    }
    assert (
        await client.put(path, headers=AUTH, json={"mode": "oops"})
    ).status_code == 422
    assert (
        await client.put(path, headers=AUTH, json={"mode": "none", "admins": ["a"]})
    ).status_code == 422
    assert (
        await client.put(
            "/api/sessions/missing/error-notifications",
            headers=AUTH,
            json={"mode": "none"},
        )
    ).status_code == 404


async def test_concurrent_runtime_patches_preserve_other_settings_and_reset(
    inspection_api,
):
    _, store, _ = inspection_api
    await store.ensure_session("test")
    await store.update_session_meta("test", {"runtime": None})
    await asyncio.gather(
        store.set_error_notifications("test", "none"),
        store.patch_runtime_settings(
            "test", {"reasoning": {"effort": "high", "show": True}}
        ),
    )
    assert (await store.get_session_meta("test"))["runtime"] == {
        "error_notifications": "none",
        "reasoning": {"effort": "high", "show": True},
    }
    await store.patch_runtime_settings("test", {"reasoning": None})
    assert (await store.get_session_meta("test"))["runtime"] == {
        "error_notifications": "none"
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/memory/overview",
        "/api/memory/items/item",
        "/api/sessions/test/error-notifications",
    ],
)
async def test_inspection_and_settings_require_operator_auth(inspection_api, path):
    client, _, _ = inspection_api
    assert (await client.get(path)).status_code == 401
    if "error-notifications" in path:
        assert (await client.put(path, json={"mode": "all"})).status_code == 401
