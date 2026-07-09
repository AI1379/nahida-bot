"""Tests for Gateway-Node protocol envelope round-tripping and fixtures.

Every fixture in ``tests/fixtures/gateway_node/*.json`` must parse into a
``NodeEnvelope`` and round-trip back to equivalent JSON. This is the
cross-language contract that the Rust/Tauri implementation will be checked
against too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nahida_bot.gateway.node_protocol.schemas import (
    NodeEnvelope,
    PROTOCOL_VERSION,
    build_event,
    build_heartbeat,
    build_request,
    build_response,
    error_from_exception,
)
from nahida_bot.gateway.node_protocol.errors import (
    CapabilityDenied,
    MethodNotFound,
    NodeProtocolError,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "gateway_node"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _fixture_names() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.glob("*.json"))


# -- Fixtures parse + round-trip ------------------------------------------


@pytest.mark.parametrize("name", _fixture_names())
def test_fixture_parses_into_envelope(name: str) -> None:
    raw = _load_fixture(name)
    envelope = NodeEnvelope.model_validate(raw)
    assert envelope.version == PROTOCOL_VERSION
    assert envelope.kind in {"request", "response", "event", "heartbeat"}


@pytest.mark.parametrize("name", _fixture_names())
def test_fixture_round_trips_to_equivalent_json(name: str) -> None:
    """Round-tripping must preserve all wire-relevant fields.

    The ``_comment`` field is intentionally dropped because it is a
    documentation-only annotation, not part of the wire format.
    """
    raw = _load_fixture(name)
    raw.pop("_comment", None)
    envelope = NodeEnvelope.model_validate(raw)
    dumped = envelope.model_dump(mode="json", exclude_none=True)
    re_parsed = NodeEnvelope.model_validate(dumped)
    assert re_parsed == envelope


def test_unknown_fields_are_ignored() -> None:
    """Forward compatibility: unknown fields must not break parsing."""
    raw = {
        "version": "1.0",
        "kind": "event",
        "event": "agent.message.completed",
        "payload": {"session_id": "s1"},
        "future_field": {"anything": True},
    }
    envelope = NodeEnvelope.model_validate(raw)
    assert envelope.event == "agent.message.completed"


# -- Builders -------------------------------------------------------------


def test_build_request_envelope() -> None:
    env = build_request("node.register", request_id="req_1", payload={"node_id": "n1"})
    assert env.kind == "request"
    assert env.id == "req_1"
    assert env.method == "node.register"
    assert env.payload == {"node_id": "n1"}


def test_build_response_envelope_ok() -> None:
    env = build_response("req_1", ok=True, payload={"accepted": True})
    assert env.kind == "response"
    assert env.id == "req_1"
    assert env.ok is True
    assert env.error is None


def test_build_response_envelope_error() -> None:
    env = build_response("req_1", ok=False)
    assert env.ok is False
    assert env.payload is None


def test_build_event_envelope() -> None:
    env = build_event("agent.message.completed", payload={"session_id": "s1"})
    assert env.kind == "event"
    assert env.event == "agent.message.completed"
    assert env.id is None


def test_build_heartbeat_envelope() -> None:
    env = build_heartbeat("ping", ts=100, echo_ts=99)
    assert env.kind == "heartbeat"
    assert env.payload == {"type": "ping", "ts": 100, "echo_ts": 99}


# -- Error mapping --------------------------------------------------------


def test_error_from_protocol_error_preserves_code() -> None:
    exc = CapabilityDenied("nope")
    obj = error_from_exception(exc)
    assert obj.code == "capability_denied"
    assert obj.retryable is False
    assert obj.message == "nope"


def test_error_from_method_not_found_is_retryable_false() -> None:
    obj = error_from_exception(MethodNotFound("x"))
    assert obj.code == "method_not_found"
    assert obj.retryable is False


def test_error_from_value_error_maps_to_invalid_arguments() -> None:
    obj = error_from_exception(ValueError("bad payload"))
    assert obj.code == "invalid_arguments"


def test_error_from_generic_exception_maps_to_internal_error() -> None:
    obj = error_from_exception(RuntimeError("boom"))
    assert obj.code == "internal_error"
    assert obj.retryable is True


def test_protocol_error_subclasses_have_distinct_codes() -> None:
    codes = {
        cls.code for cls in NodeProtocolError.__subclasses__() if hasattr(cls, "code")
    }
    # Ensure no two subclasses share a code.
    assert len(codes) == len(NodeProtocolError.__subclasses__())
