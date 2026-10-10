"""Cancellable media lab with simulated PCM and external-ASR/real-reply modes.

Client epochs invalidate output immediately, before a server round trip.
No persistent conversation writes; real response engines are injected.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import time
from dataclasses import asdict
from typing import Any, TYPE_CHECKING

from fastapi import WebSocket
import structlog

from nahida_bot.voice.session import RealtimeVoiceSession, VoiceSessionEffects

if TYPE_CHECKING:
    from nahida_bot.gateway.services.voice_reply import VoiceResponder, VoiceReply

logger = structlog.get_logger(__name__)


def audio_packet(header: dict[str, Any], pcm: bytes) -> bytes:
    """Encode a self-identifying chunk; never pair separate JSON/binary frames."""
    metadata = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("<I", len(metadata)) + metadata + pcm


def test_tone(sequence: int, *, frequency: int = 440) -> bytes:
    """20ms at 24kHz, quiet pulsed tone with a short fade to avoid clicks."""
    values = []
    for offset in range(480):
        sample = sequence * 480 + offset
        phase = sample % 6000
        envelope = max(0.0, min(1.0, phase / 240, (3600 - phase) / 240))
        values.append(
            int(2400 * envelope * math.sin(2 * math.pi * frequency * sample / 24000))
        )
    return struct.pack("<480h", *values)


class VoiceLabRuntime:
    """One socket owns one cancellable response task and one voice core."""

    def __init__(
        self,
        websocket: WebSocket,
        session_id: str,
        *,
        responder: VoiceResponder | None = None,
    ) -> None:
        self.socket = websocket
        self.core = RealtimeVoiceSession(session_id)
        self.session_id = session_id
        self.epoch = 0
        self.frames = 0
        self.response: asyncio.Task[None] | None = None
        self.output_finished = False
        self.output_started = False
        self.delay_ms = 600
        self.inject_late = False
        self.responder = responder
        self.pending_spoken_text = ""

    async def send(self, kind: str, **fields: Any) -> None:
        await self.socket.send_json({"type": kind, "epoch": self.epoch, **fields})

    async def effects(self, effects: VoiceSessionEffects | None) -> None:
        if effects is not None:
            for command in effects.reflex:
                await self.send("reflex", command=asdict(command))

    async def handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "ping":
            await self.send("pong")
            return
        epoch = event.get("epoch")
        if type(epoch) is not int or epoch < 1:
            raise ValueError("epoch must be a positive integer")
        if kind == "speech_started":
            if epoch <= self.epoch:
                return
            await self.start_turn(epoch)
            return
        if epoch != self.epoch:
            return
        turn = self.core.current_turn
        if turn is None:
            return
        await self._handle_turn_event(event, turn.turn_id)

    async def _handle_turn_event(self, event: dict[str, Any], turn_id: str) -> None:
        kind = event.get("type")
        if kind == "speech_stopped":
            if self.core.speech_stopped(turn_id):
                if self.responder is None:
                    await self.finish_input(event, turn_id)
        elif (
            kind in ("transcript_partial", "transcript_final")
            and self.responder is not None
        ):
            await self._transcript(event, turn_id)
        elif kind == "playback_started":
            self.output_started = True
            await self.effects(self.core.assistant_audio_started(turn_id))
        elif kind == "playback_done":
            await self._finish_playback(turn_id)
        elif kind == "reflex_finished":
            self.core.reflex_finished(str(event.get("command_id", "")))
        else:
            raise ValueError("unknown voice lab event")

    async def _transcript(self, event: dict[str, Any], turn_id: str) -> None:
        text = event.get("text")
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("invalid transcript")
        if event["type"] == "transcript_partial":
            self.core.update_partial(turn_id, text)
            return
        if self.core.turn_state not in {"listening", "endpointing"}:
            return
        if not text.strip():
            await self.effects(self.core.complete_turn(turn_id))
            await self.send("idle")
            return
        await self.effects(
            self.core.finalize_transcript(
                turn_id,
                text.strip(),
                now_ms=int(time.time() * 1000),
            )
        )
        await self.send("transcript", text=text.strip())
        self.response = asyncio.create_task(
            self.produce_real(text.strip(), turn_id, self.epoch)
        )

    async def _finish_playback(self, turn_id: str) -> None:
        if not self.output_finished or not self.output_started:
            return
        if self.responder is not None and self.pending_spoken_text:
            self.responder.played(self.pending_spoken_text)
            self.pending_spoken_text = ""
        await self.effects(self.core.complete_turn(turn_id))
        await self.send("idle")

    async def start_turn(self, epoch: int) -> None:
        old_turn = self.core.current_turn
        old_epoch = self.epoch
        await self.cancel_response()
        self.epoch = epoch
        self.output_finished = False
        self.output_started = False
        self.pending_spoken_text = ""
        await self.effects(self.core.speech_started())
        turn = self.core.current_turn
        assert turn is not None
        await self.send("listening", turn_id=turn.turn_id)
        # Deliberate protocol probe: client must reject this superseded packet.
        if self.inject_late and old_turn is not None:
            await self.socket.send_bytes(
                audio_packet(
                    {
                        "epoch": old_epoch,
                        "turn_id": old_turn.turn_id,
                        "sequence": 999,
                        "sample_rate_hz": 24000,
                    },
                    test_tone(0),
                )
            )

    async def finish_input(self, event: dict[str, Any], turn_id: str) -> None:
        delay = event.get("delay_ms", 600)
        if type(delay) is not int or not 0 <= delay <= 3000:
            raise ValueError("delay_ms must be between 0 and 3000")
        self.delay_ms = delay
        self.inject_late = event.get("inject_late") is True
        text = "[模拟识别] 用户结束了一次发言（未运行 ASR）"
        await self.effects(
            self.core.finalize_transcript(
                turn_id,
                text,
                now_ms=int(time.time() * 1000),
            )
        )
        await self.send("transcript", text=text, frames_received=self.frames)
        self.response = asyncio.create_task(self.produce(turn_id, self.epoch))

    async def produce(self, turn_id: str, epoch: int) -> None:
        deadline = time.monotonic() + self.delay_ms / 1000
        while time.monotonic() < deadline:
            command = self.core.pop_due_reflex(now_ms=int(time.time() * 1000))
            if command is not None:
                await self.send("reflex", command=asdict(command))
            await asyncio.sleep(min(0.02, max(0, deadline - time.monotonic())))
        await self.send("output", turn_id=turn_id, text="[模拟输出] 3 秒脉冲测试音")
        for sequence in range(150):
            await self.socket.send_bytes(
                audio_packet(
                    {
                        "epoch": epoch,
                        "turn_id": turn_id,
                        "sequence": sequence,
                        "sample_rate_hz": 24000,
                    },
                    test_tone(sequence),
                )
            )
            await asyncio.sleep(0.02)
        self.output_finished = True
        await self.send("output_done", turn_id=turn_id)

    async def _reply_with_reflexes(self, text: str) -> VoiceReply:
        assert self.responder is not None
        task = asyncio.create_task(self.responder.respond(text))
        try:
            async with asyncio.timeout(120):
                while not task.done():
                    await asyncio.wait({task}, timeout=0.05)
                    command = self.core.pop_due_reflex(now_ms=int(time.time() * 1000))
                    if command is not None:
                        await self.send("reflex", command=asdict(command))
                return task.result()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def produce_real(self, text: str, turn_id: str, epoch: int) -> None:
        try:
            reply = await self._reply_with_reflexes(text)
        except Exception as exc:
            # Log only exception categories; provider payloads may contain secrets.
            logger.warning(
                "voice.reply_failed",
                session_id=self.session_id,
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
            )
            await self.effects(self.core.complete_turn(turn_id))
            await self.send(
                "turn_error", message="Gateway 模型或 TTS 请求失败，请检查服务配置。"
            )
            return
        if self.epoch != epoch:
            return
        self.pending_spoken_text = reply.text
        await self.send("output", text=reply.text, mode="artifact")
        # This is explicitly a complete artifact, not incremental synthesis.
        self.output_finished = True
        await self.socket.send_bytes(
            audio_packet(
                {
                    "kind": "artifact",
                    "epoch": epoch,
                    "turn_id": turn_id,
                    "sequence": 0,
                    "mime_type": reply.audio.mime_type,
                },
                reply.audio.data,
            )
        )

    async def receive(self) -> None:
        """Enforce small bounded messages; retain only an input frame counter."""
        while True:
            message = await asyncio.wait_for(self.socket.receive(), timeout=60)
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data is not None:
                if self.responder is not None:
                    raise ValueError(
                        "external ASR mode accepts transcripts, not microphone audio"
                    )
                if len(data) != 640:
                    raise ValueError(
                        "input must be 20ms mono PCM16 at 16kHz (640 bytes)"
                    )
                self.frames += 1
            else:
                raw = message.get("text", "")
                if len(raw) > 4096:
                    raise ValueError("control message too large")
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("control message must be an object")
                await self.handle(event)
            if self.response is not None and self.response.done():
                # Surface producer failures instead of leaving an unobserved task.
                self.response.result()

    async def cancel_response(self) -> None:
        task, self.response = self.response, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        await self.cancel_response()
        self.core.close()
