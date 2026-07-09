"""Tests for NodeAuthService: token issuance, verification, pairing and revocation."""

from __future__ import annotations

import time


from nahida_bot.gateway.services.node_auth import (
    InMemoryNodeTokenStore,
    NodeAuthService,
)


def test_issue_and_verify_node_token() -> None:
    svc = NodeAuthService()
    full_token, token_id = svc.issue_node_token(node_id="desktop-1")

    principal = svc.verify(full_token)
    assert principal is not None
    assert principal.node_id == "desktop-1"
    assert principal.token_id == token_id
    assert principal.token_type == "node"


def test_verify_rejects_garbage_token() -> None:
    svc = NodeAuthService()
    assert svc.verify("not-a-token") is None
    assert svc.verify("") is None
    assert svc.verify("nt_x.secret") is None  # unknown token_id


def test_verify_rejects_revoked_token() -> None:
    svc = NodeAuthService()
    full_token, token_id = svc.issue_node_token(node_id="n1")
    assert svc.revoke(token_id) is True

    assert svc.verify(full_token) is None


def test_revoke_unknown_token_returns_false() -> None:
    svc = NodeAuthService()
    assert svc.revoke("does-not-exist") is False


def test_revoke_all_for_node() -> None:
    svc = NodeAuthService()
    svc.issue_node_token(node_id="n1")
    svc.issue_node_token(node_id="n1")
    svc.issue_node_token(node_id="n2")

    assert svc.revoke_all_for_node("n1") == 2
    assert all(rec.revoked for rec in svc.list_tokens("n1"))
    # n2 untouched
    assert not svc.list_tokens("n2")[0].revoked


def test_pairing_token_is_single_use() -> None:
    svc = NodeAuthService()
    full_token, _ = svc.issue_pairing_token(node_id="n1")

    principal = svc.verify(full_token)
    assert principal is not None
    assert principal.token_type == "pairing"

    # Second use fails.
    assert svc.verify(full_token) is None


def test_pairing_token_exchanges_for_node_token() -> None:
    svc = NodeAuthService()
    pairing_token, _ = svc.issue_pairing_token(node_id="desktop-1", scope=("live2d",))

    result = svc.exchange_pairing_for_node_token(pairing_token)
    assert result is not None
    node_full_token, node_token_id = result

    principal = svc.verify(node_full_token)
    assert principal is not None
    assert principal.node_id == "desktop-1"
    assert principal.token_type == "node"
    assert "live2d" in principal.scope

    # Pairing token cannot be exchanged again.
    assert svc.exchange_pairing_for_node_token(pairing_token) is None


def test_pairing_exchange_rejects_node_token_input() -> None:
    svc = NodeAuthService()
    node_token, _ = svc.issue_node_token(node_id="n1")
    assert svc.exchange_pairing_for_node_token(node_token) is None


def test_node_token_expiry() -> None:
    svc = NodeAuthService(default_ttl_seconds=0)  # no global expiry
    full_token, _ = svc.issue_node_token(node_id="n1", ttl_seconds=1)

    assert svc.verify(full_token) is not None
    # Force expiry.
    record = list(svc.store.list_by_node("n1"))[0]
    record.expires_at = time.time() - 1
    assert svc.verify(full_token) is None


def test_in_memory_store_put_get_delete() -> None:
    store = InMemoryNodeTokenStore()
    from nahida_bot.gateway.services.node_auth import NodeTokenRecord

    rec = NodeTokenRecord(token_id="t1", node_id="n1", token_digest="d")
    store.put("t1", rec)
    assert store.get("t1") is rec
    assert len(store.list_by_node("n1")) == 1
    assert store.delete("t1") is True
    assert store.get("t1") is None
    assert store.delete("t1") is False


def test_token_digest_not_stored_in_plaintext() -> None:
    svc = NodeAuthService()
    full_token, _ = svc.issue_node_token(node_id="n1")
    record = list(svc.store.list_by_node("n1"))[0]
    # The stored digest must not be the raw token or its secret half.
    assert record.token_digest != full_token
    assert "." not in record.token_digest  # digest is a hex sha256
