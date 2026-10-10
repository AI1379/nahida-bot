"""Small authenticated WAV-over-HTTP host, independent of the Bot Gateway."""

import asyncio
import hmac
import io
import threading
import wave

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from nahida_bot.voice.asr import AsrProvider, AsrRequest, AsrResult

MAX_AUDIO_BYTES = 16000 * 2 * 30


def decode_wav(data: bytes, language: str) -> AsrRequest:
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (
                1,
                2,
                16000,
            ):
                raise ValueError("expected mono PCM16 WAV at 16kHz")
            frames = audio.getnframes()
            if not 1 <= frames <= 16000 * 30:
                raise ValueError("ASR audio must be between 1 sample and 30 seconds")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("truncated WAV data")
        return AsrRequest(pcm=pcm, language=language)
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid PCM WAV") from exc


def create_asr_app(provider: AsrProvider, token: str) -> FastAPI:
    if not token:
        raise ValueError("ASR host requires a token")
    app = FastAPI(title="Nahida ASR host")
    # Every request still needs the dedicated ASR bearer; never use Gateway keys.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    inference_lock = threading.Lock()

    def recognize(body: AsrRequest) -> AsrResult:
        # Ownership remains with the inference thread even if HTTP is cancelled.
        if not inference_lock.acquire(blocking=False):
            raise HTTPException(429, "ASR is still processing another utterance")
        try:
            return provider.transcribe(body)
        finally:
            inference_lock.release()

    @app.post("/v1/asr/transcriptions")
    async def transcribe(request: Request, language: str = "zh") -> dict[str, str]:
        provided = request.headers.get("authorization", "")
        if not hmac.compare_digest(provided.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "Invalid ASR token")
        if language not in {"zh", "en", "ja", "auto"}:
            raise HTTPException(422, "language must be zh, en, ja or auto")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_AUDIO_BYTES + 4096:
                raise HTTPException(413, "ASR audio exceeds 30 seconds")
            data.extend(chunk)
        try:
            body = decode_wav(bytes(data), "" if language == "auto" else language)
            result = await asyncio.to_thread(recognize, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"text": result.text[:2000], "language": result.language}

    return app
