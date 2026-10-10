"""Real-mode wiring with controlled model/TTS, including interrupted history."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nahida_bot.gateway.services.voice_reply import GatewayVoiceResponder, VoiceReply
from nahida_bot.speech.base import SpeechArtifact
from nahida_bot.voice.lab import VoiceLabRuntime


def state():
    model = SimpleNamespace(content="你好，我在听。")
    chat = AsyncMock(return_value=model)
    route = SimpleNamespace(
        slot=SimpleNamespace(
            provider=SimpleNamespace(chat=chat), default_model="test-model"
        ),
        model="",
    )
    service = SimpleNamespace(
        config=SimpleNamespace(voices={"nahida": {}}),
        synthesize=AsyncMock(
            return_value=SpeechArtifact(
                data=b"wav", mime_type="audio/wav", duration_ms=100
            )
        ),
    )
    value = SimpleNamespace(
        application=SimpleNamespace(
            model_router=SimpleNamespace(resolve_for_task=lambda *args, **kw: route),
            workspace_manager=None,
            settings=SimpleNamespace(system_prompt="Persona baseline"),
        ),
        generate_config=SimpleNamespace(model="primary"),
        speech_service=service,
        speech_config=SimpleNamespace(max_text_length=150, max_concurrency=2),
    )
    return value, chat, service.synthesize


@pytest.mark.asyncio
async def test_gateway_voice_configuration_and_only_played_assistant_history():
    config, chat, synthesize = state()
    responder = GatewayVoiceResponder(config, "nahida")
    first = await responder.respond("你好")
    # First answer interrupted: it must not enter the next model context.
    await responder.respond("等一下")
    messages = chat.call_args.kwargs["messages"]
    assert not any(message.role == "assistant" for message in messages)
    assert [message.content for message in messages if message.role == "user"] == [
        "你好",
        "等一下",
    ]
    responder.played(first.text)
    await responder.respond("继续")
    assert any(
        message.role == "assistant" for message in chat.call_args.kwargs["messages"]
    )
    assert synthesize.call_args.kwargs["voice"] == "nahida"
    assert synthesize.call_args.kwargs["output_format"] == "audio/wav"


@pytest.mark.asyncio
async def test_interrupt_cancels_model_tts_and_late_transcripts_do_not_start_work():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_reply(text):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    responder = SimpleNamespace(respond=slow_reply, played=MagicMock())
    socket = SimpleNamespace(send_json=AsyncMock(), send_bytes=AsyncMock())
    runtime = VoiceLabRuntime(socket, "test", responder=responder)
    await runtime.handle({"type": "speech_started", "epoch": 1})
    await runtime.handle({"type": "transcript_final", "epoch": 1, "text": "你好"})
    await asyncio.wait_for(started.wait(), 1)
    await runtime.handle({"type": "speech_started", "epoch": 2})
    assert cancelled.is_set()
    await runtime.handle({"type": "transcript_final", "epoch": 1, "text": "late"})
    assert runtime.response is None
    assert not socket.send_bytes.called
    assert not responder.played.called
    await runtime.close()


@pytest.mark.asyncio
async def test_real_turn_waits_for_final_transcript_and_playback_ack():
    reply = VoiceReply(
        "回答", SpeechArtifact(data=b"wav", mime_type="audio/wav", duration_ms=100)
    )
    responder = SimpleNamespace(
        respond=AsyncMock(return_value=reply), played=MagicMock()
    )
    socket = SimpleNamespace(send_json=AsyncMock(), send_bytes=AsyncMock())
    runtime = VoiceLabRuntime(socket, "test", responder=responder)
    await runtime.handle({"type": "speech_started", "epoch": 1})
    await runtime.handle({"type": "transcript_partial", "epoch": 1, "text": "部"})
    await runtime.handle({"type": "speech_stopped", "epoch": 1})
    assert not responder.respond.called
    await runtime.handle({"type": "transcript_final", "epoch": 1, "text": "完整文本"})
    await runtime.response
    assert socket.send_bytes.called
    assert not responder.played.called
    await runtime.handle({"type": "playback_started", "epoch": 1})
    await runtime.handle({"type": "playback_done", "epoch": 1})
    responder.played.assert_called_once_with("回答")
    await runtime.close()
