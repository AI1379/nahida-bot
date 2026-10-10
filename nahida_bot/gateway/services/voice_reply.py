"""Gateway-configured model + artifact TTS for the first real voice experiment.

Uses a bounded in-memory conversation. Only confirmed, fully played assistant
replies enter that history. Agent tools and persistent spoken memory are later
integration steps, not silently borrowed from the text-message pipeline.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from nahida_bot.agent.context import ContextMessage
from nahida_bot.gateway.routes.generate import _persona_messages
from nahida_bot.gateway.routes.speech import SpeechJobRequest, _synthesize_with_limits
from nahida_bot.speech.base import SpeechArtifact
from nahida_bot.workspace.exceptions import WorkspaceError


@dataclass(frozen=True, slots=True)
class VoiceReply:
    text: str
    audio: SpeechArtifact


class VoiceResponder(Protocol):
    async def respond(self, text: str) -> VoiceReply: ...
    def played(self, text: str) -> None: ...


class GatewayVoiceResponder:
    def __init__(self, state: Any, voice: str = "") -> None:
        self._state = state
        self._voice = voice
        self._history: list[ContextMessage] = []
        if getattr(state, "speech_service", None) is None:
            raise ValueError("Gateway webapi.speech is not configured")
        config = state.speech_service.config
        if voice and voice not in config.voices:
            raise ValueError("Unknown Gateway voice name")

    async def respond(self, text: str) -> VoiceReply:
        application = self._state.application
        config = self._state.generate_config
        routed = application.model_router.resolve_for_task(
            "text_generate",
            explicit=getattr(config, "model", ""),
            default_spec="primary",
            fallback="default",
        )
        if routed is None:
            raise ValueError("Gateway has no configured chat model")
        workspace_id = ""
        manager = getattr(application, "workspace_manager", None)
        if manager is not None:
            try:
                workspace_id = manager.get_active_workspace().workspace_id
            except WorkspaceError:
                pass
        self._history.append(ContextMessage(role="user", content=text, source="voice"))
        self._history = self._history[-12:]
        max_chars = min(200, self._state.speech_config.max_text_length)
        messages = [
            *_persona_messages(application, workspace_id),
            ContextMessage(
                role="system",
                source="voice_mode",
                content=(
                    f"当前为单人语音通话实验。用自然简短的口语回答，最多{max_chars}字。"
                    "只输出要朗读的正文，不要 Markdown。本模式没有工具，不要声称已执行外部操作。"
                ),
            ),
            *self._history,
        ]
        response = await routed.slot.provider.chat(
            messages=messages,
            model=routed.model or routed.slot.default_model,
        )
        answer = (response.content or "").strip()[:max_chars]
        if not answer:
            raise ValueError("Empty voice reply")
        audio = await _synthesize_with_limits(
            self._state.speech_service,
            None,
            self._state.speech_config,
            SpeechJobRequest(text=answer, voice=self._voice, output_format="audio/wav"),
        )
        if not audio.data or len(audio.data) > 8 * 1024 * 1024:
            raise ValueError("Voice artifact is empty or too large")
        if audio.mime_type not in {
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
            "audio/mpeg",
            "audio/flac",
        }:
            raise ValueError("Voice artifact must be a decodable audio container")
        return VoiceReply(answer, audio)

    def played(self, text: str) -> None:
        self._history.append(
            ContextMessage(role="assistant", content=text, source="voice_played")
        )
        self._history = self._history[-12:]
