"""Test the transport against a provider contract, with no model downloads."""

import io
import wave
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from nahida_bot.voice.asr import AsrProvider, AsrRequest, AsrResult
from nahida_bot.voice.asr_host import create_asr_app


def wav(rate=16000, frames=320):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(bytes(frames * 2))
    return buffer.getvalue()


class Provider(AsrProvider):
    def __init__(self):
        self.requests = []

    def transcribe(self, request: AsrRequest) -> AsrResult:
        self.requests.append(request)
        return AsrResult("你好", "zh")


def test_wav_transport_passes_normalized_pcm_to_any_provider():
    provider = Provider()
    with TestClient(create_asr_app(provider, "asr-only")) as client:
        response = client.post(
            "/v1/asr/transcriptions?language=auto",
            content=wav(),
            headers={"Authorization": "Bearer asr-only"},
        )
        assert response.json() == {"text": "你好", "language": "zh"}
        assert provider.requests == [AsrRequest(bytes(640), language="")]


def test_asr_rejects_wrong_token_and_invalid_audio_before_inference():
    provider = Provider()
    with TestClient(create_asr_app(provider, "asr-only")) as client:
        route = "/v1/asr/transcriptions"
        assert client.post(route, content=wav()).status_code == 401
        headers = {"Authorization": "Bearer asr-only"}
        assert (
            client.post(route, content=wav(48000), headers=headers).status_code == 422
        )
        assert (
            client.post(route, content=wav(frames=480001), headers=headers).status_code
            == 422
        )
        assert (
            client.post(route, content=wav()[:-3], headers=headers).status_code == 422
        )
        assert not provider.requests


def test_asr_never_runs_two_inferences_at_once():
    entered, release = threading.Event(), threading.Event()

    class Blocking(Provider):
        def transcribe(self, request):
            entered.set()
            assert release.wait(5)
            return super().transcribe(request)

    with TestClient(create_asr_app(Blocking(), "asr-only")) as client:

        def call():
            return client.post(
                "/v1/asr/transcriptions",
                content=wav(),
                headers={"Authorization": "Bearer asr-only"},
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(call)
            try:
                assert entered.wait(3)
                assert call().status_code == 429
            finally:
                release.set()
            assert pending.result().status_code == 200
