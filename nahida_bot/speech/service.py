"""Unified speech service: provider registry, dispatch and voice resolution.

Part A of ``docs/design/desktop-app.md`` §9.3.1. Channel consumers and the
``speak`` tool call :meth:`SpeechService.synthesize`; the service resolves the
configured voice → backend → provider adapter and returns a
:class:`SpeechArtifact`. Part B (ArtifactStore / Gateway Media / Desktop
playback) is deferred.
"""

from __future__ import annotations

from typing import Any

import httpx

from nahida_bot.speech.base import SpeechArtifact, SpeechRequest, TtsError, TtsProvider
from nahida_bot.speech.config import TtsConfig
from nahida_bot.speech.providers.gpt_sovits import GPTSoVITSProvider

# Built-in provider adapter classes keyed by their ``type`` discriminator.
# GPTSoVITSProvider inherits TtsProvider; pyright's ``type[ABC]`` assignability
# is conservative about constructor signatures (the ABC declares ``__init__``
# taking ``Any`` so subclasses match), but the real inheritance makes this a
# sound nominal subclass relationship.
_BUILTIN_PROVIDERS: dict[str, type[TtsProvider]] = {
    GPTSoVITSProvider.type: GPTSoVITSProvider,
}


class SpeechService:
    """Dispatches synthesis requests to the configured TTS provider adapter."""

    def __init__(
        self,
        config: TtsConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        providers: dict[str, type[TtsProvider]] | None = None,
    ) -> None:
        self._config = config
        self._shared_client = http_client
        self._adapter_classes: dict[str, type[TtsProvider]] = dict(_BUILTIN_PROVIDERS)
        if providers:
            self._adapter_classes.update(providers)
        # Lazily-instantiated provider clients keyed by backend name.
        self._clients: dict[str, TtsProvider] = {}

    @property
    def config(self) -> TtsConfig:
        return self._config

    def register_provider(self, adapter_cls: type[TtsProvider]) -> None:
        """Register an additional provider adapter by its ``type`` discriminator."""

        self._adapter_classes[adapter_cls.type] = adapter_cls

    def supported_provider_types(self) -> list[str]:
        return sorted(self._adapter_classes)

    def resolve_provider_type(self, voice: str = "") -> str:
        """Return the provider type discriminator for ``voice`` without I/O.

        Used by callers (REST routes) that need to compute a cache key
        *before* paying for synthesis: the cache key includes ``provider`` so
        switching backends invalidates stale audio, but the provider is
        derivable from voice + config alone.
        """
        try:
            _voice_name, backend_name, _voice_raw = self._config.resolve_voice(voice)
            _backend_name, backend_raw = self._config.backend_raw(backend_name)
        except ValueError:
            return ""
        return str(backend_raw.get("type", "")).strip()

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        text_lang: str = "",
        style: str = "",
        speed: float = 0.0,
        pitch: float = 0.0,
        output_format: str = "",
    ) -> SpeechArtifact:
        """Synthesize ``text`` using the resolved voice and its backend.

        ``text_lang``/``speed``/``output_format`` override the voice/backend
        defaults when non-empty/non-zero. Raises :class:`TtsError` on failure.
        """

        voice_name, backend_name, voice_raw = self._config.resolve_voice(voice)
        _backend_name, backend_raw = self._config.backend_raw(backend_name)
        provider_type = str(backend_raw.get("type", "")).strip()
        adapter_cls = self._adapter_classes.get(provider_type)
        if adapter_cls is None:
            raise TtsError(
                "tts_unsupported_provider",
                (
                    f"TTS provider type {provider_type!r} has no registered adapter. "
                    f"Registered: {', '.join(self.supported_provider_types()) or '(none)'}"
                ),
                provider=provider_type,
                backend=backend_name,
            )

        provider = await self._get_or_create_client(
            backend_name, provider_type, backend_raw
        )
        voice_config = adapter_cls.parse_voice_config(voice_raw)
        request = SpeechRequest(
            text=text,
            text_lang=text_lang.strip(),
            style=style,
            speed=speed,  # 0.0 = unset; provider applies its backend default
            pitch=pitch,
            output_format=output_format,
        )
        try:
            artifact = await provider.synthesize(request, voice_config)
        except TtsError as exc:
            if not exc.backend:
                exc.backend = backend_name
            raise
        # Attach resolved voice name for downstream attribution.
        return SpeechArtifact(
            data=artifact.data,
            mime_type=artifact.mime_type,
            duration_ms=artifact.duration_ms,
            provider=artifact.provider or provider_type,
            voice=voice_name,
        )

    async def close(self) -> None:
        """Close all lazily-created provider clients."""

        for client in self._clients.values():
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()

    async def _get_or_create_client(
        self,
        backend_name: str,
        provider_type: str,
        backend_raw: dict[str, Any],
    ) -> TtsProvider:
        cached = self._clients.get(backend_name)
        if cached is not None:
            return cached
        adapter_cls = self._adapter_classes[provider_type]
        backend_config = adapter_cls.parse_backend_config(backend_raw)
        client = adapter_cls(backend_config, http_client=self._shared_client)
        self._clients[backend_name] = client
        return client
