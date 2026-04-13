"""Tests for reasoning extraction utilities (Phase 2.8)."""

from __future__ import annotations

from nahida_bot.agent.providers.reasoning import (
    ReasoningPolicy,
    _ReasoningMixin,
    extract_think_tags,
)


# ── extract_think_tags ──


class TestExtractThinkTags:
    def test_no_tags_returns_original(self) -> None:
        content = "Just a normal response."
        cleaned, reasoning = extract_think_tags(content)
        assert cleaned == content
        assert reasoning is None

    def test_single_think_tag(self) -> None:
        content = "<think>Step 1: analyze\nStep 2: conclude</think>The answer is 42."
        cleaned, reasoning = extract_think_tags(content)
        assert cleaned == "The answer is 42."
        assert reasoning == "Step 1: analyze\nStep 2: conclude"

    def test_single_thinking_tag(self) -> None:
        content = "<thinking>Deep thought</thinking>Result."
        cleaned, reasoning = extract_think_tags(content)
        assert cleaned == "Result."
        assert reasoning == "Deep thought"

    def test_multiple_tags(self) -> None:
        content = "<think>First</think>Middle<think>Second</think>End"
        cleaned, reasoning = extract_think_tags(content)
        assert cleaned == "MiddleEnd"
        assert reasoning == "First\nSecond"

    def test_empty_content(self) -> None:
        cleaned, reasoning = extract_think_tags("")
        assert cleaned == ""
        assert reasoning is None

    def test_whitespace_only_tag(self) -> None:
        content = "<think>   </think>Content."
        cleaned, reasoning = extract_think_tags(content)
        # Empty tag body is stripped → no reasoning
        assert cleaned == "Content."
        assert reasoning is None

    def test_multiline_in_tags(self) -> None:
        content = "<think>\nLine 1\nLine 2\n</think>\nFinal answer."
        cleaned, reasoning = extract_think_tags(content)
        assert "Final answer." in cleaned
        assert "Line 1" in reasoning  # type: ignore[operator]
        assert "Line 2" in reasoning  # type: ignore[operator]


# ── _ReasoningMixin ──


class TestReasoningMixin:
    def _make_mixin(self, reasoning_key: str = "reasoning_content") -> _ReasoningMixin:
        mixin = _ReasoningMixin()
        mixin.reasoning_key = reasoning_key
        return mixin

    def test_native_field_extraction(self) -> None:
        mixin = self._make_mixin()
        message = {
            "content": "The answer is 42.",
            "reasoning_content": "Step by step analysis...",
        }
        reasoning, cleaned = mixin._extract_reasoning_from_message(message)
        assert reasoning == "Step by step analysis..."
        assert cleaned is None  # native field found, no cleaning needed

    def test_custom_reasoning_key(self) -> None:
        mixin = self._make_mixin(reasoning_key="reasoning")
        message = {
            "content": "Hello",
            "reasoning": "Custom reasoning field",
        }
        reasoning, cleaned = mixin._extract_reasoning_from_message(message)
        assert reasoning == "Custom reasoning field"
        assert cleaned is None

    def test_tag_fallback(self) -> None:
        mixin = self._make_mixin()
        message = {
            "content": "<think>Thinking...</think>The answer.",
        }
        reasoning, cleaned = mixin._extract_reasoning_from_message(message)
        assert reasoning == "Thinking..."
        assert cleaned == "The answer."

    def test_native_takes_priority_over_tags(self) -> None:
        mixin = self._make_mixin()
        message = {
            "content": "<think>In-content</think>Text",
            "reasoning_content": "Native field value",
        }
        reasoning, cleaned = mixin._extract_reasoning_from_message(message)
        assert reasoning == "Native field value"
        assert cleaned is None  # native field wins, no tag stripping

    def test_no_reasoning(self) -> None:
        mixin = self._make_mixin()
        message = {"content": "Just a plain response."}
        reasoning, cleaned = mixin._extract_reasoning_from_message(message)
        assert reasoning is None
        assert cleaned is None

    def test_empty_native_field_falls_through_to_tags(self) -> None:
        mixin = self._make_mixin()
        message = {
            "content": "<think>Fallback</think>Text",
            "reasoning_content": "   ",  # whitespace-only
        }
        reasoning, cleaned = mixin._extract_reasoning_from_message(message)
        assert reasoning == "Fallback"
        assert cleaned == "Text"


# ── ReasoningPolicy ──


class TestReasoningPolicy:
    def test_enum_values(self) -> None:
        assert ReasoningPolicy.STRIP.value == "strip"
        assert ReasoningPolicy.APPEND.value == "append"
        assert ReasoningPolicy.BUDGET.value == "budget"
