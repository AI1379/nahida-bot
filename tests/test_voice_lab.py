"""Exercise the real socket protocol, cancellation and connection ownership."""

import json
import struct
from unittest.mock import MagicMock
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nahida_bot.gateway.routes.voice import router
from nahida_bot.gateway.app import WebAPIApp
from nahida_bot.core.config import Settings
from nahida_bot.speech.base import SpeechArtifact


@pytest.fixture
def client():
    app = FastAPI()
    app.state.auth_token = "lab-test-secret"
    app.state.voice_lab_connections = set()
    app.include_router(router)
    with TestClient(app) as value:
        yield value


def test_gateway_registers_the_lab_endpoint():
    application = MagicMock(spec=["settings"])
    application.settings = Settings(db_path=":memory:", plugin_paths=[])
    gateway = WebAPIApp(application, auth_token="lab-test-secret")
    with TestClient(gateway.fastapi_app) as test_client:
        with test_client.websocket_connect("/api/voice/lab") as socket:
            socket.send_json({"token": "lab-test-secret"})
            assert socket.receive_json()["type"] == "ready"


def _until(socket, kind):
    for _ in range(250):
        message = socket.receive()
        assert message["type"] != "websocket.close", message
        if "text" in message:
            event = json.loads(message["text"])
            if event["type"] == kind:
                return event
    pytest.fail(f"did not receive {kind}")


def _packet(socket):
    for _ in range(250):
        message = socket.receive()
        if "bytes" in message:
            data = message["bytes"]
            size = struct.unpack_from("<I", data)[0]
            return json.loads(data[4 : 4 + size]), data[4 + size :]
    pytest.fail("did not receive audio")


@pytest.mark.parametrize("token", ["wrong", "", "汉字"])
def test_auth_required(client, token):
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": token})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4401
    assert not client.app.state.voice_lab_connections


def test_unconfigured_token_does_not_enable_lab(client):
    client.app.state.auth_token = ""
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": ""})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4401


def test_binary_authentication_is_rejected_cleanly(client):
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_bytes(bytes(640))
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4400


def test_connection_limit(client):
    client.app.state.voice_lab_connections.update({"one", "two", "three", "four"})
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": "lab-test-secret"})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4429


def test_input_and_barge_in_with_deliberately_late_packet(client):
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": "lab-test-secret"})
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        socket.send_bytes(bytes(640))
        socket.send_json({"type": "speech_started", "epoch": 1})
        first = _until(socket, "listening")
        socket.send_json(
            {"type": "speech_stopped", "epoch": 1, "delay_ms": 0, "inject_late": True}
        )
        assert _until(socket, "transcript")["frames_received"] == 1
        header, pcm = _packet(socket)
        assert header["turn_id"] == first["turn_id"]
        assert len(pcm) == 960
        socket.send_json({"type": "playback_started", "epoch": 1})
        socket.send_json({"type": "speech_started", "epoch": 2})
        second = _until(socket, "listening")
        assert second["turn_id"] != first["turn_id"]
        header, _ = _packet(socket)
        assert header["epoch"] == 1 and header["sequence"] == 999
        # A stale stop event must not finalize the new listening turn.
        socket.send_json({"type": "speech_stopped", "epoch": 1, "delay_ms": 0})
        socket.send_json({"type": "speech_stopped", "epoch": 2, "delay_ms": 0})
        assert _until(socket, "transcript")["epoch"] == 2
        header, _ = _packet(socket)
        assert header["epoch"] == 2 and header["sequence"] == 0
    assert not client.app.state.voice_lab_connections


def test_turn_waits_for_actual_playback_completion(client):
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": "lab-test-secret"})
        socket.receive_json()
        socket.send_json({"type": "speech_started", "epoch": 1})
        _until(socket, "listening")
        socket.send_json({"type": "speech_stopped", "epoch": 1, "delay_ms": 0})
        _packet(socket)
        socket.send_json({"type": "playback_started", "epoch": 1})
        _until(socket, "output_done")
        socket.send_json({"type": "playback_done", "epoch": 1})
        assert _until(socket, "idle")["epoch"] == 1


@pytest.mark.parametrize("payload", [bytes(639), bytes(642)])
def test_invalid_pcm_closes_connection(client, payload):
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": "lab-test-secret"})
        socket.receive_json()
        socket.send_bytes(payload)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4400
    assert not client.app.state.voice_lab_connections


def test_real_mode_requires_gateway_tts(client):
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json({"token": "lab-test-secret", "mode": "external_asr"})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4403


def test_external_asr_text_runs_gateway_model_and_configured_tts(client):
    chat = AsyncMock(return_value=SimpleNamespace(content="你好"))
    routed = SimpleNamespace(
        model="model", slot=SimpleNamespace(provider=SimpleNamespace(chat=chat))
    )
    synthesize = AsyncMock(
        return_value=SpeechArtifact(
            data=b"complete-wav", mime_type="audio/wav", duration_ms=100
        )
    )
    client.app.state.application = SimpleNamespace(
        model_router=SimpleNamespace(resolve_for_task=lambda *args, **kwargs: routed),
        workspace_manager=None,
        settings=SimpleNamespace(system_prompt="persona"),
    )
    client.app.state.generate_config = SimpleNamespace(model="primary")
    client.app.state.speech_service = SimpleNamespace(
        config=SimpleNamespace(voices={"nahida": {}}), synthesize=synthesize
    )
    client.app.state.speech_config = SimpleNamespace(
        max_text_length=200, max_concurrency=3
    )
    with client.websocket_connect("/api/voice/lab") as socket:
        socket.send_json(
            {"token": "lab-test-secret", "mode": "external_asr", "voice": "nahida"}
        )
        assert socket.receive_json()["tts_mode"] == "artifact"
        socket.send_json({"type": "speech_started", "epoch": 1})
        _until(socket, "listening")
        socket.send_json({"type": "speech_stopped", "epoch": 1})
        socket.send_json(
            {"type": "transcript_final", "epoch": 1, "text": "真实识别文本"}
        )
        header, data = _packet(socket)
        assert header["kind"] == "artifact" and data == b"complete-wav"
        assert synthesize.call_args.kwargs["voice"] == "nahida"
        assert chat.call_args.kwargs["messages"][-1].content == "真实识别文本"
        socket.send_json({"type": "playback_started", "epoch": 1})
        socket.send_json({"type": "playback_done", "epoch": 1})
        _until(socket, "idle")
        socket.send_bytes(bytes(640))
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4400
