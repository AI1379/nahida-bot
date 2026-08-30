from __future__ import annotations

import base64
import json
import time

from nahida_bot.auth.codex import (
    TokenResponse,
    _parse_jwt_claims,
    _parse_token_response,
    extract_account_id,
    token_needs_refresh,
    to_codex_token,
)
from nahida_bot.db.repositories.sqlite_codex_token_repo import CodexToken


def _make_jwt(claims: dict[str, object]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{payload}.signature"


def test_parse_jwt_claims_returns_claims_dict() -> None:
    token = _make_jwt({"chatgpt_account_id": "acct_123", "email": "u@x"})
    assert _parse_jwt_claims(token) == {
        "chatgpt_account_id": "acct_123",
        "email": "u@x",
    }


def test_parse_jwt_claims_returns_empty_for_non_jwt() -> None:
    assert _parse_jwt_claims("not-a-jwt") == {}
    assert _parse_jwt_claims("a.b") == {}
    assert _parse_jwt_claims("a.b.c.d") == {}


def test_parse_jwt_claims_returns_empty_for_garbage_payload() -> None:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode("ascii")
    payload = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode("ascii")
    assert _parse_jwt_claims(f"{header}.{payload}.sig") == {}


def test_extract_account_id_prefers_top_level_claim() -> None:
    tokens = TokenResponse(
        access_token="",
        refresh_token="r",
        id_token=_make_jwt({"chatgpt_account_id": "acct_top"}),
        expires_in=3600,
    )
    assert extract_account_id(tokens) == "acct_top"


def test_extract_account_id_falls_back_to_nested_claim() -> None:
    tokens = TokenResponse(
        access_token="",
        refresh_token="r",
        id_token=_make_jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_nested"}}
        ),
        expires_in=3600,
    )
    assert extract_account_id(tokens) == "acct_nested"


def test_extract_account_id_falls_back_to_access_token() -> None:
    tokens = TokenResponse(
        access_token=_make_jwt({"chatgpt_account_id": "acct_from_access"}),
        refresh_token="r",
        id_token="",
        expires_in=3600,
    )
    assert extract_account_id(tokens) == "acct_from_access"


def test_extract_account_id_falls_back_to_first_organization() -> None:
    tokens = TokenResponse(
        access_token="",
        refresh_token="r",
        id_token=_make_jwt({"organizations": [{"id": "org_1"}, {"id": "org_2"}]}),
        expires_in=3600,
    )
    assert extract_account_id(tokens) == "org_1"


def test_extract_account_id_returns_empty_when_no_claim_present() -> None:
    tokens = TokenResponse(
        access_token="garbage",
        refresh_token="r",
        id_token="also-garbage",
        expires_in=3600,
    )
    assert extract_account_id(tokens) == ""


def test_to_codex_token_populates_account_id_and_expiry() -> None:
    tokens = TokenResponse(
        access_token="access-abc",
        refresh_token="refresh-xyz",
        id_token=_make_jwt({"chatgpt_account_id": "acct_t"}),
        expires_in=1000,
    )
    before = int(time.time())
    codex_token = to_codex_token(tokens)
    after = int(time.time())

    assert codex_token.refresh_token == "refresh-xyz"
    assert codex_token.access_token == "access-abc"
    assert codex_token.account_id == "acct_t"
    assert before + 1000 <= codex_token.expires_at <= after + 1000


def test_parse_refresh_response_preserves_unrotated_refresh_token() -> None:
    tokens = _parse_token_response(
        {"access_token": "new-access", "expires_in": 3600},
        fallback_refresh_token="old-refresh",
    )

    assert tokens.access_token == "new-access"
    assert tokens.refresh_token == "old-refresh"


def test_token_needs_refresh_true_for_missing_access_token() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="",
        expires_at=int(time.time()) + 10_000,
        account_id="a",
    )
    assert token_needs_refresh(token) is True


def test_token_needs_refresh_true_when_within_safety_margin() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="x",
        expires_at=int(time.time()) + 30,
        account_id="a",
    )
    assert token_needs_refresh(token) is True


def test_token_needs_refresh_false_when_plenty_of_time_left() -> None:
    token = CodexToken(
        refresh_token="r",
        access_token="x",
        expires_at=int(time.time()) + 10_000,
        account_id="a",
    )
    assert token_needs_refresh(token) is False
