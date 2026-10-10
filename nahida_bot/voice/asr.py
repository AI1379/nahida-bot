"""Location-neutral utterance ASR contract; streaming has its own contract.

Providers accept normalized PCM, not filesystem paths or transport requests.
An HTTP host, Gateway, or future voice adapter can use the same provider.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True, slots=True)
class AsrRequest:
    pcm: bytes
    sample_rate_hz: int = 16000
    language: str = "zh"


@dataclass(frozen=True, slots=True)
class AsrResult:
    text: str
    language: str


class AsrProvider(ABC):
    """Blocking utterance recognition; hosts must run this off their event loop."""

    @abstractmethod
    def transcribe(self, request: AsrRequest) -> AsrResult:
        raise NotImplementedError


class FasterWhisperAsrProvider(AsrProvider):
    """Optional CPU backend. Install dependencies only in the ASR host env."""

    def __init__(self, model: str, *, threads: int = 4) -> None:
        whisper = import_module("faster_whisper")
        self._numpy = import_module("numpy")
        self._model = whisper.WhisperModel(
            model,
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
        )

    def transcribe(self, request: AsrRequest) -> AsrResult:
        if request.sample_rate_hz != 16000 or len(request.pcm) % 2:
            raise ValueError("ASR expects mono PCM16 at 16kHz")
        samples = (
            self._numpy.frombuffer(request.pcm, dtype="<i2").astype("float32") / 32768
        )
        segments, info = self._model.transcribe(
            samples,
            language=request.language or None,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return AsrResult(
            "".join(segment.text for segment in segments).strip(), info.language
        )
