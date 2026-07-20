"""MotionPlan domain types — the server-side output of a MotionPlanner.

A MotionPlan is the semantic analysis of an agent reply: it splits the text
into segments and tags each with emotion / motion / voice metadata. The plan
is attached to ``OutboundMessage.extra["display_plan"]`` and forwarded to the
Desktop node, where the PetRuntime + TTS pipeline consume it directly.

The wire format matches what the Desktop DisplayPlan parser expects
(``desktop/src/domain/displayPlan.ts``): ``{version, text, segments[]}`` with
snake_case fields on the wire, camelCase inside Desktop after normalization.

This module is deliberately free of LLM/provider imports so it can be unit
tested in isolation and reused by future planner implementations (embedding,
ONNX, etc.).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ── Enums (kept as plain sets for fast validation) ──────────────────────

DISPLAY_EMOTIONS: frozenset[str] = frozenset(
    {"neutral", "happy", "thinking", "worried", "error", "offline"}
)

DISPLAY_MOTIONS: frozenset[str] = frozenset(
    {"idle", "nod", "point", "wave", "notify", "speaking", "emerge", "retreat"}
)

VOICE_STYLES: frozenset[str] = frozenset({"neutral", "bright", "calm", "soft"})

_MAX_TEXT = 4000
_MAX_SEGMENT_TEXT = 800
_MAX_SEGMENTS = 12
_MAX_PAUSE_MS = 3000


def _clean_emotion(value: Any) -> str:
    e = str(value or "").strip().lower()
    return e if e in DISPLAY_EMOTIONS else "neutral"


def _clean_motion(value: Any) -> str:
    m = str(value or "").strip().lower()
    return m if m in DISPLAY_MOTIONS else "idle"


def _clean_voice_style(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in VOICE_STYLES else ""


def _clean_text(value: Any, max_len: int = _MAX_SEGMENT_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_len]


def _clamp_number(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


@dataclass(slots=True, frozen=True)
class MotionSegment:
    """One sentence/clause of the reply with its performance metadata."""

    text: str
    emotion: str = "neutral"
    motion: str = "idle"
    voice_style: str = ""
    voice_speed: float = 1.0
    voice_pitch: float = 0.0
    pause_after_ms: int = 0

    def to_display_dict(self) -> dict[str, Any]:
        """Wire-format dict for the Desktop DisplayPlan parser."""
        d: dict[str, Any] = {
            "text": self.text,
            "emotion": self.emotion,
            "motion": self.motion,
        }
        if self.voice_style:
            d["voice"] = {
                "style": self.voice_style,
                "speed": self.voice_speed,
                "pitch": self.voice_pitch,
            }
        if self.pause_after_ms:
            d["pause_after_ms"] = self.pause_after_ms
        return d


@dataclass(slots=True, frozen=True)
class MotionPlan:
    """The full display plan for one agent reply."""

    text: str
    segments: tuple[MotionSegment, ...]

    def to_display_plan_dict(self) -> dict[str, Any]:
        """Convert to the wire format consumed by the Desktop DisplayPlan parser."""
        return {
            "version": "1.0",
            "text": self.text,
            "segments": [s.to_display_dict() for s in self.segments],
        }

    @classmethod
    def from_llm_json(cls, raw: str, original_text: str) -> MotionPlan | None:
        """Parse the JSON output of the LLM motion planner.

        Returns ``None`` when the output is not usable (no valid JSON, no
        segments, or all segments are empty). The caller should fall back to
        a neutral single-segment plan in that case.
        """
        candidate = _extract_json(raw)
        if candidate is None:
            return None
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None

        raw_segments = data.get("segments") if isinstance(data, dict) else None
        if not isinstance(raw_segments, list):
            # Maybe the LLM returned a bare list of segments.
            if isinstance(data, list):
                raw_segments = data
            else:
                return None

        segments: list[MotionSegment] = []
        for raw_seg in raw_segments[:_MAX_SEGMENTS]:
            if not isinstance(raw_seg, dict):
                continue
            seg_text = _clean_text(raw_seg.get("text"))
            if not seg_text:
                continue
            voice_raw = raw_seg.get("voice")
            voice_style = ""
            voice_speed = 1.0
            voice_pitch = 0.0
            if isinstance(voice_raw, dict):
                voice_style = _clean_voice_style(voice_raw.get("style"))
                voice_speed = _clamp_number(voice_raw.get("speed"), 1.0, 0.5, 1.5)
                voice_pitch = _clamp_number(voice_raw.get("pitch"), 0.0, -6.0, 6.0)

            segments.append(
                MotionSegment(
                    text=seg_text,
                    emotion=_clean_emotion(raw_seg.get("emotion")),
                    motion=_clean_motion(raw_seg.get("motion")),
                    voice_style=voice_style,
                    voice_speed=voice_speed,
                    voice_pitch=voice_pitch,
                    pause_after_ms=int(
                        _clamp_number(
                            raw_seg.get("pause_after_ms")
                            or raw_seg.get("pauseAfterMs"),
                            0,
                            0,
                            _MAX_PAUSE_MS,
                        )
                    ),
                )
            )

        if not segments:
            return None

        combined_text = _clean_text(original_text, _MAX_TEXT) or " ".join(
            s.text for s in segments
        )
        return cls(text=combined_text, segments=tuple(segments))

    @classmethod
    def neutral(cls, text: str) -> MotionPlan:
        """Build a single-segment neutral plan (fallback when planner fails)."""
        clean = _clean_text(text, _MAX_TEXT) or text[:_MAX_TEXT]
        return cls(
            text=clean,
            segments=(
                MotionSegment(
                    text=clean,
                    emotion="neutral",
                    motion="speaking",
                    voice_style="neutral",
                ),
            ),
        )


def _extract_json(raw: str) -> str | None:
    """Try to isolate a JSON object/array from an LLM text response."""
    trimmed = raw.strip()
    if not trimmed:
        return None
    # Strip markdown code fences.
    if trimmed.startswith("```"):
        lines = trimmed.split("\n")
        # Remove first line (```json or ```) and last line (```).
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        trimmed = "\n".join(lines).strip()
    # Find the outermost { } or [ ].
    brace_start = trimmed.find("{")
    bracket_start = trimmed.find("[")
    if brace_start < 0 and bracket_start < 0:
        return None
    if brace_start < 0:
        start = bracket_start
    elif bracket_start < 0:
        start = brace_start
    else:
        start = min(brace_start, bracket_start)
    # Find the matching closing bracket from the end.
    brace_end = trimmed.rfind("}")
    bracket_end = trimmed.rfind("]")
    if brace_end < 0 and bracket_end < 0:
        return None
    if brace_end < 0:
        end = bracket_end
    elif bracket_end < 0:
        end = brace_end
    else:
        end = max(brace_end, bracket_end)
    return trimmed[start : end + 1]


__all__ = [
    "MotionPlan",
    "MotionSegment",
    "DISPLAY_EMOTIONS",
    "DISPLAY_MOTIONS",
    "VOICE_STYLES",
]
