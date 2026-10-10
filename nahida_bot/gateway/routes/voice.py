"""Authenticated, connection-scoped Voice Lab WebSocket.

The admin credential travels in the first frame, never in a URL. Lab access
requires a configured admin token, even on otherwise unauthenticated Gateways.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from nahida_bot.voice.lab import VoiceLabRuntime
from nahida_bot.gateway.services.voice_reply import GatewayVoiceResponder

router = APIRouter(prefix="/api/voice", tags=["voice-lab"])


async def _authenticate(socket: WebSocket) -> dict[str, Any] | None:
    message = await asyncio.wait_for(socket.receive(), timeout=5)
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect()
    raw = message.get("text")
    if raw is None or len(raw) > 4096:
        raise ValueError("expected a small JSON authentication frame")
    auth = json.loads(raw)
    configured = socket.app.state.auth_token
    token = auth.get("token") if isinstance(auth, dict) else None
    valid = (
        bool(configured)
        and isinstance(token, str)
        and hmac.compare_digest(
            token.encode(),
            configured.encode(),
        )
    )
    return auth if valid else None


@router.websocket("/lab")
async def voice_lab(socket: WebSocket) -> None:
    await socket.accept()
    runtime: VoiceLabRuntime | None = None
    slots: set[str] = socket.app.state.voice_lab_connections
    session_id = uuid4().hex
    try:
        auth = await _authenticate(socket)
        if auth is None:
            await socket.close(code=4401, reason="Voice Lab requires an admin token")
            return
        if len(slots) >= 4:
            await socket.close(code=4429, reason="Voice Lab connection limit reached")
            return
        slots.add(session_id)
        mode = auth.get("mode", "mock")
        if not isinstance(mode, str) or mode not in {"mock", "external_asr"}:
            raise ValueError("unsupported voice mode")
        voice = auth.get("voice", "")
        if not isinstance(voice, str) or len(voice) > 128:
            raise ValueError("invalid voice name")
        try:
            responder = (
                GatewayVoiceResponder(socket.app.state, voice)
                if mode == "external_asr"
                else None
            )
        except ValueError as exc:
            await socket.close(code=4403, reason=str(exc))
            return
        runtime = VoiceLabRuntime(socket, session_id, responder=responder)
        await runtime.send(
            "ready",
            session_id=session_id,
            mode=mode,
            tts_mode="artifact" if responder else "mock",
        )
        async with asyncio.timeout(1800):
            await runtime.receive()
    except WebSocketDisconnect:
        pass
    except (ValueError, TimeoutError):
        await socket.close(
            code=4400, reason="Invalid Voice Lab message or session timeout"
        )
    finally:
        if runtime is not None:
            await runtime.close()
        slots.discard(session_id)
