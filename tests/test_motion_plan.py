"""Unit tests for MotionPlan parsing and sanitization."""

from __future__ import annotations


from nahida_bot.agent.motion_plan import (
    MotionPlan,
    MotionSegment,
)


def test_neutral_plan_is_usable_as_display_plan() -> None:
    plan = MotionPlan.neutral("今天的计划已经整理好了。")
    d = plan.to_display_plan_dict()

    assert d["version"] == "1.0"
    assert d["text"] == "今天的计划已经整理好了。"
    assert len(d["segments"]) == 1
    seg = d["segments"][0]
    assert seg["text"] == "今天的计划已经整理好了。"
    assert seg["emotion"] == "neutral"
    assert seg["motion"] == "speaking"
    assert seg["voice"]["style"] == "neutral"


def test_from_llm_json_parses_valid_output() -> None:
    raw = """{"segments":[
        {"text":"你好","emotion":"happy","motion":"wave","voice":{"style":"bright","speed":1.1,"pitch":2},"pause_after_ms":200},
        {"text":"今天天气不错","emotion":"neutral","motion":"point","voice":{"style":"calm","speed":0.9},"pause_after_ms":0}
    ]}"""
    plan = MotionPlan.from_llm_json(raw, original_text="你好。今天天气不错。")
    assert plan is not None
    assert len(plan.segments) == 2
    assert plan.segments[0].emotion == "happy"
    assert plan.segments[0].motion == "wave"
    assert plan.segments[0].voice_style == "bright"
    assert plan.segments[0].voice_speed == 1.1
    assert plan.segments[0].voice_pitch == 2.0
    assert plan.segments[0].pause_after_ms == 200
    assert plan.segments[1].emotion == "neutral"
    assert plan.segments[1].voice_pitch == 0.0


def test_from_llm_json_handles_markdown_fence() -> None:
    raw = """```json
{"segments":[{"text":"你好","emotion":"happy","motion":"nod"}]}
```"""
    plan = MotionPlan.from_llm_json(raw, original_text="你好")
    assert plan is not None
    assert plan.segments[0].text == "你好"


def test_from_llm_json_sanitizes_garbage_values() -> None:
    raw = """{"segments":[
        {"text":"ok","emotion":"ecstatic","motion":"backflip","voice":{"speed":99,"pitch":99}},
        {"text":"ok2"},
        {}
    ]}"""
    plan = MotionPlan.from_llm_json(raw, original_text="ok ok2")
    assert plan is not None
    assert len(plan.segments) == 2
    assert plan.segments[0].emotion == "neutral"  # invalid cleaned
    assert plan.segments[0].motion == "idle"
    assert plan.segments[0].voice_speed == 1.5  # clamped
    assert plan.segments[0].voice_pitch == 6.0  # clamped
    assert plan.segments[1].text == "ok2"


def test_from_llm_json_returns_none_for_empty_output() -> None:
    assert MotionPlan.from_llm_json("", "text") is None
    assert MotionPlan.from_llm_json("not json at all", "text") is None
    assert MotionPlan.from_llm_json('{"segments":[]}', "text") is None


def test_to_display_plan_skips_default_voice() -> None:
    seg = MotionSegment(text="hi", emotion="thinking", motion="idle")
    d = seg.to_display_dict()
    assert "voice" not in d  # no voice_style → omitted
    assert d["emotion"] == "thinking"


def test_uses_original_text_when_segments_diverge() -> None:
    raw = """{"segments":[{"text":"A"},{"text":"B"}]}"""
    plan = MotionPlan.from_llm_json(raw, original_text="original text")
    assert plan is not None
    assert plan.text == "original text"


class TestMotionSegment:
    pass  # inline tests above already cover segment-level logic


class TestMotionPlan:
    pass
