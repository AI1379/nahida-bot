"""Tests for the speak (TTS) plugin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nahida_bot.core.chat_address import ChatAddress
from nahida_bot.core.context import SessionContext, current_session
from nahida_bot.plugins.base import OutboundMessage
from nahida_bot.plugins.tts.plugin import TtsPlugin
from nahida_bot.speech.base import SpeechArtifact, TtsError
from nahida_bot_sdk.manifest import PluginManifest
from nahida_bot_sdk.testing import RecordingMockBotAPI, load_plugin_for_test

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


class _TtsAPI(RecordingMockBotAPI):
    def __init__(self, workspace_root: Path) -> None:
        super().__init__()
        self.workspace_root = workspace_root
        self.sent_messages: list[tuple[str, OutboundMessage, str]] = []

    async def send_message(
        self,
        target: str,
        message: OutboundMessage,
        *,
        channel: str = "",
    ) -> str:
        self.sent_messages.append((target, message, channel))
        return f"msg-{len(self.sent_messages)}"

    def get_workspace_root(self, workspace_id: str | None = None) -> str:
        return str(self.workspace_root)

    def resolve_workspace_path(self, path: str) -> str:
        return str(self.workspace_root / path)


class _FakeSpeechService:
    """Stand-in for SpeechService; returns a canned artifact or raises."""

    def __init__(
        self,
        *,
        artifact: SpeechArtifact | None = None,
        error: TtsError | None = None,
    ) -> None:
        self.calls: list[dict[str, str]] = []
        self._artifact = artifact or SpeechArtifact(
            data=WAV_BYTES,
            mime_type="audio/wav",
            provider="gpt-sovits-v2",
            voice="nahida",
        )
        self._error = error

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        text_lang: str = "",
        **_: Any,
    ) -> SpeechArtifact:
        self.calls.append({"text": text, "voice": voice, "text_lang": text_lang})
        if self._error is not None:
            raise self._error
        return self._artifact

    async def close(self) -> None:
        pass


def _manifest(config: dict[str, Any] | None = None) -> PluginManifest:
    base: dict[str, Any] = {
        "default_backend": "default",
        "backends": {"default": {"type": "gpt-sovits-v2", "base_url": "http://x:9880"}},
        "voices": {
            "nahida": {
                "ref_audio_path": "/n.wav",
                "prompt_text": "hi",
                "prompt_lang": "zh",
            }
        },
        "default_voice": "nahida",
        "output_dir": "generated/audio",
    }
    if config:
        base.update(config)
    return PluginManifest(
        id="tts",
        name="TTS",
        version="0.1.0",
        entrypoint="nahida_bot.plugins.tts.plugin:TtsPlugin",
        config=base,
    )


def _session() -> SessionContext:
    return SessionContext(
        platform="telegram",
        chat_id="123",
        session_id="telegram:private:123",
        workspace_id="default",
        chat_address=ChatAddress(
            channel="telegram", target_type="private", target_id="123"
        ),
    )


async def _load_plugin(
    api: _TtsAPI,
    *,
    config: dict[str, Any] | None = None,
    service: _FakeSpeechService | None = None,
) -> tuple[TtsPlugin, _FakeSpeechService]:
    plugin = TtsPlugin(api=api, manifest=_manifest(config))
    await load_plugin_for_test(plugin)
    fake = service or _FakeSpeechService()
    # Swap the real SpeechService (built in on_load) for the fake.
    plugin._service = fake  # type: ignore[assignment]
    return plugin, fake


# ── registration ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plugin_registers_command_and_tool(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    await _load_plugin(api)
    assert "speak" in api.registered_commands
    assert "说话" in api.registered_commands
    assert "speak" in api.registered_tools


@pytest.mark.asyncio
async def test_framework_enabled_is_not_a_business_config_gate(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    await _load_plugin(api, config={"enabled": False})
    assert "speak" in api.registered_commands
    assert "speak" in api.registered_tools


# ── speak tool: success ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speak_tool_synthesizes_saves_and_sends(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, fake = await _load_plugin(api)
    token = current_session.set(_session())
    try:
        result = json.loads(await plugin._tool_speak("你好呀"))
    finally:
        current_session.reset(token)

    # plugin defers voice resolution to SpeechService (no persona → "" → default_voice)
    assert fake.calls[0]["text"] == "你好呀"
    assert fake.calls[0]["voice"] == ""

    # voice attachment sent
    assert len(api.sent_messages) == 1
    attachment = api.sent_messages[0][1].attachments[0]
    assert attachment.type == "voice"
    assert attachment.path.endswith(".wav")
    assert attachment.mime_type == "audio/wav"

    # wav file written to workspace
    assert (tmp_path / "generated/audio").is_dir()
    saved = list((tmp_path / "generated/audio").glob("voice-*.wav"))
    assert len(saved) == 1

    # payload contract (§6.2 / §11.3)
    assert result["status"] == "ok"
    assert result["delivered_text"] == "你好呀"
    assert result["audio"]["mime_type"] == "audio/wav"
    assert result["audio"]["voice"] == "nahida"
    assert result["media"][0]["kind"] == "audio"
    assert result["media"][0]["metadata"]["source_tool"] == "speak"
    assert result["sent_message_ids"] == ["msg-1"]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_speak_tool_send_false_does_not_send(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, _ = await _load_plugin(api)
    token = current_session.set(_session())
    try:
        result = json.loads(await plugin._tool_speak("hi", send=False))
    finally:
        current_session.reset(token)

    assert api.sent_messages == []
    assert result["delivered_text"] == ""
    assert result["audio"]["path"].endswith(".wav")
    assert "sent_message_ids" not in result


@pytest.mark.asyncio
async def test_speak_passes_text_lang_to_service(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, fake = await _load_plugin(api)
    token = current_session.set(_session())
    try:
        await plugin._tool_speak("こんにちは", text_lang="ja")
    finally:
        current_session.reset(token)
    assert fake.calls[0]["text_lang"] == "ja"


# ── speak tool: degrade on failure ──────────────────────────────────────


@pytest.mark.asyncio
async def test_speak_degrades_to_text_on_synthesis_failure(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    failing = _FakeSpeechService(error=TtsError("tts_synthesis_failed", "backend down"))
    plugin, _ = await _load_plugin(api, service=failing)
    token = current_session.set(_session())
    try:
        result = json.loads(await plugin._tool_speak("你好呀"))
    finally:
        current_session.reset(token)

    # no voice sent; plain text sent instead
    assert len(api.sent_messages) == 1
    assert api.sent_messages[0][1].text == "你好呀"
    assert api.sent_messages[0][1].attachments == []

    assert result["status"] == "degraded"
    assert result["fallback"] == "text"
    assert result["delivered_text"] == "你好呀"
    assert result["code"] == "tts_synthesis_failed"


@pytest.mark.asyncio
async def test_speak_degrade_no_send_returns_no_delivered_text(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    failing = _FakeSpeechService(error=TtsError("tts_synthesis_failed", "boom"))
    plugin, _ = await _load_plugin(api, service=failing)
    token = current_session.set(_session())
    try:
        result = json.loads(await plugin._tool_speak("你好呀", send=False))
    finally:
        current_session.reset(token)

    assert api.sent_messages == []
    assert result["status"] == "degraded"
    assert result["delivered_text"] == ""


# ── quota ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quota_exceeded_after_limit(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, _ = await _load_plugin(api, config={"max_calls_per_24h": 1})
    token = current_session.set(_session())
    try:
        first = json.loads(await plugin._tool_speak("first"))
        second = json.loads(await plugin._tool_speak("second"))
    finally:
        current_session.reset(token)

    assert first["status"] == "ok"
    assert second["status"] == "error"
    assert second["code"] == "tts_quota_exceeded"
    assert "1/1" in second["error"]


@pytest.mark.asyncio
async def test_quota_released_on_failure_does_not_consume(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    failing = _FakeSpeechService(error=TtsError("tts_synthesis_failed", "boom"))
    plugin, _ = await _load_plugin(
        api, config={"max_calls_per_24h": 1}, service=failing
    )
    token = current_session.set(_session())
    try:
        first = json.loads(await plugin._tool_speak("fails"))  # consumes, then releases
        second = json.loads(
            await plugin._tool_speak("fails")
        )  # should still be allowed
    finally:
        current_session.reset(token)

    assert first["status"] == "degraded"
    assert second["status"] == "degraded"  # quota not consumed by failed attempts


# ── truncation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_long_text_is_truncated(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, fake = await _load_plugin(api, config={"max_text_length": 5})
    token = current_session.set(_session())
    try:
        result = json.loads(await plugin._tool_speak("你好呀世界再见"))
    finally:
        current_session.reset(token)

    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert fake.calls[0]["text"] == "你好呀世界"  # first 5 chars


@pytest.mark.asyncio
async def test_empty_text_returns_error(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, fake = await _load_plugin(api)
    token = current_session.set(_session())
    try:
        result = json.loads(await plugin._tool_speak("   "))
    finally:
        current_session.reset(token)

    assert result["status"] == "error"
    assert fake.calls == []


# ── lifecycle ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disable_releases_service(tmp_path: Path) -> None:
    api = _TtsAPI(tmp_path)
    plugin, _ = await _load_plugin(api)
    await plugin.on_disable()
    assert plugin._service is None
    assert plugin._semaphore is None
