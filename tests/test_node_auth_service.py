"""Tests for NodeAuthService: token issuance, verification, pairing and revocation."""

from __future__ import annotations

import time


from nahida_bot.gateway.services.node_auth import (
    InMemoryNodeTokenStore,
    NodeAuthService,
)


async def test_issue_and_verify_node_token() -> None:
    svc = NodeAuthService()
    full_token, token_id = await svc.issue_node_token(node_id="desktop-1")

    principal = await svc.verify(full_token)
    assert principal is not None
    assert principal.node_id == "desktop-1"
    assert principal.token_id == token_id
    assert principal.token_type == "node"


async def test_verify_rejects_garbage_token() -> None:
    svc = NodeAuthService()
    assert await svc.verify("not-a-token") is None
    assert await svc.verify("") is None
    assert await svc.verify("nt_x.secret") is None  # unknown token_id


async def test_verify_rejects_revoked_token() -> None:
    svc = NodeAuthService()
    full_token, token_id = await svc.issue_node_token(node_id="n1")
    assert await svc.revoke(token_id) is True

    assert await svc.verify(full_token) is None


async def test_revoke_unknown_token_returns_false() -> None:
    svc = NodeAuthService()
    assert await svc.revoke("does-not-exist") is False


async def test_revoke_all_for_node() -> None:
    svc = NodeAuthService()
    await svc.issue_node_token(node_id="n1")
    await svc.issue_node_token(node_id="n1")
    await svc.issue_node_token(node_id="n2")

    assert await svc.revoke_all_for_node("n1") == 2
    assert all(rec.revoked for rec in await svc.list_tokens("n1"))
    # n2 untouched
    assert not (await svc.list_tokens("n2"))[0].revoked


async def test_pairing_token_is_single_use() -> None:
    svc = NodeAuthService()
    full_token, _ = await svc.issue_pairing_token(node_id="n1")

    principal = await svc.verify(full_token)
    assert principal is not None
    assert principal.token_type == "pairing"

    # Second use fails.
    assert await svc.verify(full_token) is None


async def test_pairing_token_exchanges_for_node_token() -> None:
    svc = NodeAuthService()
    pairing_token, _ = await svc.issue_pairing_token(
        node_id="desktop-1", scope=("live2d",)
    )

    result = await svc.exchange_pairing_for_node_token(pairing_token)
    assert result is not None
    node_full_token, node_token_id = result

    principal = await svc.verify(node_full_token)
    assert principal is not None
    assert principal.node_id == "desktop-1"
    assert principal.token_type == "node"
    assert "live2d" in principal.scope

    # Pairing token cannot be exchanged again.
    assert await svc.exchange_pairing_for_node_token(pairing_token) is None


async def test_pairing_exchange_rejects_node_token_input() -> None:
    svc = NodeAuthService()
    node_token, _ = await svc.issue_node_token(node_id="n1")
    assert await svc.exchange_pairing_for_node_token(node_token) is None


async def test_pairing_preserves_actor_and_conversation_binding() -> None:
    svc = NodeAuthService()
    pairing_token, _ = await svc.issue_pairing_token(
        node_id="desktop-local",
        actor_account_key="desktop:user:owner",
        conversation_id="conversation:private:owner-desktop",
    )

    exchanged = await svc.exchange_pairing_for_node_token(pairing_token)

    assert exchanged is not None
    node_token, _ = exchanged
    principal = await svc.verify(node_token)
    assert principal is not None
    assert principal.node_id == "desktop-local"
    assert principal.actor_account_key == "desktop:user:owner"
    assert principal.conversation_id == "conversation:private:owner-desktop"


async def test_node_token_expiry() -> None:
    svc = NodeAuthService(default_ttl_seconds=0)  # no global expiry
    full_token, _ = await svc.issue_node_token(node_id="n1", ttl_seconds=1)

    assert await svc.verify(full_token) is not None
    # Force expiry.
    record = list(await svc.store.list_by_node("n1"))[0]
    record.expires_at = time.time() - 1
    assert await svc.verify(full_token) is None


async def test_in_memory_store_put_get_delete() -> None:
    store = InMemoryNodeTokenStore()
    from nahida_bot.gateway.services.node_auth import NodeTokenRecord

    rec = NodeTokenRecord(token_id="t1", node_id="n1", token_digest="d")
    await store.put("t1", rec)
    assert await store.get("t1") is rec
    assert len(await store.list_by_node("n1")) == 1
    assert await store.delete("t1") is True
    assert await store.get("t1") is None
    assert await store.delete("t1") is False


async def test_token_digest_not_stored_in_plaintext() -> None:
    svc = NodeAuthService()
    full_token, _ = await svc.issue_node_token(node_id="n1")
    record = list(await svc.store.list_by_node("n1"))[0]
    # The stored digest must not be the raw token or its secret half.
    assert record.token_digest != full_token
    assert "." not in record.token_digest  # digest is a hex sha256
