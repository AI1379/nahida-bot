"""Tests for the deterministic Desktop motion training-data preparer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.prepare_motion_training_data import (
    DatasetPreparationError,
    prepare_datasets,
    prepare_from_directories,
    stable_split,
)


def _intent(decision_id: str, intent: str = "explain") -> dict:
    return {
        "schemaVersion": 1,
        "type": "motion_decision",
        "timestamp": "2026-08-12T00:00:00Z",
        "decisionId": decision_id,
        "assistantText": "我来说明一下。",
        "runtimeStatus": "speaking",
        "selectedIntent": {"intent": intent, "intensity": 0.4},
        "source": "rule",
        "modelId": "test-model",
        "modelProfileVersion": "default-v1",
        "plannerVersion": "rule-v1",
        "cacheHit": False,
    }


def _execution(decision_id: str) -> dict:
    plan_id = f"{decision_id}:plan"
    return {
        "schemaVersion": 1,
        "type": "motion_execution",
        "timestamp": "2026-08-12T00:00:01Z",
        "decisionId": decision_id,
        "motionPlanId": plan_id,
        "intent": {"intent": "explain"},
        "modelId": "test-model",
        "modelProfileVersion": "default-v1",
        "primitive": "explain-small",
        "validationStatus": "accepted",
        "validationWarnings": [],
        "fallbackUsed": False,
        "motionPlan": {"id": plan_id},
        "normalizedClip": {"id": f"{plan_id}:clip", "frames": []},
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_prepare_joins_records_and_keeps_splits_deterministic() -> None:
    decision = _intent("decision-1")
    execution = _execution("decision-1")
    plan_id = execution["motionPlanId"]
    records = {
        "decisions": [decision],
        "executions": [execution],
        "preferences": [
            {
                "schemaVersion": 1,
                "type": "motion_preference",
                "timestamp": "2026-08-12T00:00:02Z",
                "preferenceId": "single",
                "candidateA": plan_id,
                "labels": ["good"],
            },
            {
                "schemaVersion": 1,
                "type": "motion_preference",
                "timestamp": "2026-08-12T00:00:03Z",
                "preferenceId": "pair",
                "candidateA": plan_id,
                "candidateB": plan_id,
                "winner": plan_id,
                "labels": ["more_natural"],
            },
        ],
        "invalid": [],
    }

    prepared = prepare_datasets(records)
    split = stable_split("decision-1")

    assert prepared["planner"][split][0]["target"]["intent"] == "explain"
    assert prepared["curves"][stable_split(plan_id)][0]["motionPlan"] == {"id": plan_id}
    assert [item["preferenceId"] for item in prepared["preferences"]] == ["pair"]
    assert [item["preferenceId"] for item in prepared["feedback"]] == ["single"]
    assert prepared["manifest"]["orphanExecutionIds"] == []


def test_prepare_applies_corrections_and_ignores_retracted_feedback() -> None:
    decision = _intent("decision-correction")
    execution = _execution("decision-correction")
    plan_id = execution["motionPlanId"]
    feedback = {
        "schemaVersion": 1,
        "type": "motion_preference",
        "timestamp": "2026-08-12T00:00:02Z",
        "preferenceId": "feedback-old",
        "candidateA": plan_id,
        "labels": ["wrong_emotion"],
        "correction": {"intent": "apology", "emotion": "worried"},
    }
    retraction = {
        "schemaVersion": 1,
        "type": "motion_preference_retraction",
        "timestamp": "2026-08-12T00:00:03Z",
        "retractionId": "retraction-1",
        "retractsPreferenceId": "feedback-old",
        "motionPlanId": plan_id,
    }
    replacement = {
        **feedback,
        "timestamp": "2026-08-12T00:00:04Z",
        "preferenceId": "feedback-new",
        "correction": {"intent": "thinking", "intensity": 0.25},
    }

    prepared = prepare_datasets(
        {
            "decisions": [decision],
            "executions": [execution],
            "preferences": [feedback, retraction, replacement],
        }
    )
    sample = prepared["planner"][stable_split("decision-correction")][0]

    assert sample["target"]["intent"] == "thinking"
    assert sample["target"]["intensity"] == 0.25
    assert sample["metadata"]["humanCorrected"] is True
    assert prepared["manifest"]["curveSampleCount"] == 0
    assert prepared["manifest"]["retractedPreferenceCount"] == 1


def test_prepare_excludes_previews_and_negative_curve_targets() -> None:
    pet_decision = _intent("pet-decision")
    pet_execution = _execution("pet-decision")
    pet_execution["playbackSurface"] = "pet"
    preview_decision = _intent("preview-decision", "greet")
    preview_execution = _execution("preview-decision")
    preview_execution["playbackSurface"] = "workbench"
    feedback = {
        "schemaVersion": 1,
        "type": "motion_preference",
        "timestamp": "2026-08-12T00:00:02Z",
        "preferenceId": "too-much-feedback",
        "candidateA": pet_execution["motionPlanId"],
        "labels": ["too_much"],
        "playbackSurface": "pet",
    }

    prepared = prepare_datasets(
        {
            "decisions": [pet_decision, preview_decision],
            "executions": [pet_execution, preview_execution],
            "preferences": [feedback],
        }
    )

    assert prepared["manifest"]["plannerSampleCount"] == 1
    assert prepared["manifest"]["curveSampleCount"] == 0
    assert prepared["manifest"]["orphanExecutionIds"] == []
    assert [item["preferenceId"] for item in prepared["feedback"]] == [
        "too-much-feedback"
    ]


def test_prepare_excludes_feedback_rated_from_a_workbench_replay() -> None:
    decision = _intent("pet-decision")
    execution = _execution("pet-decision")
    execution["playbackSurface"] = "pet"
    feedback = {
        "schemaVersion": 1,
        "type": "motion_preference",
        "timestamp": "2026-08-12T00:00:02Z",
        "preferenceId": "workbench-replay-feedback",
        "candidateA": execution["motionPlanId"],
        "labels": ["good"],
        "playbackSurface": "pet",
        "ratedSurface": "workbench",
        "replayOf": execution["motionPlanId"],
    }

    prepared = prepare_datasets(
        {
            "decisions": [decision],
            "executions": [execution],
            "preferences": [feedback],
        }
    )

    assert prepared["manifest"]["plannerSampleCount"] == 1
    assert prepared["feedback"] == []


def test_prepare_does_not_supervise_an_uncorrected_bad_decision() -> None:
    decision = _intent("bad-decision")
    execution = _execution("bad-decision")
    feedback = {
        "schemaVersion": 1,
        "type": "motion_preference",
        "timestamp": "2026-08-12T00:00:02Z",
        "preferenceId": "bad-feedback",
        "candidateA": execution["motionPlanId"],
        "labels": ["bad"],
    }

    prepared = prepare_datasets(
        {
            "decisions": [decision],
            "executions": [execution],
            "preferences": [feedback],
        }
    )

    assert prepared["manifest"]["plannerSampleCount"] == 0
    assert prepared["manifest"]["curveSampleCount"] == 0


def test_prepare_reads_date_suffixed_exports_and_writes_manifest(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "export"
    output_dir = tmp_path / "prepared"
    input_dir.mkdir()
    _write_jsonl(input_dir / "decisions-2026-08-12.jsonl", [_intent("d-1")])
    _write_jsonl(input_dir / "executions-2026-08-12.jsonl", [_execution("d-1")])

    manifest = prepare_from_directories(input_dir, output_dir)

    assert manifest["plannerSampleCount"] == 1
    assert json.loads((output_dir / "manifest.json").read_text("utf-8")) == manifest
    assert (output_dir / "planner-train.jsonl").is_file()


def test_prepare_rejects_duplicate_decision_ids() -> None:
    with pytest.raises(DatasetPreparationError, match="Duplicate decisionId"):
        prepare_datasets(
            {
                "decisions": [_intent("duplicate"), _intent("duplicate")],
                "executions": [],
            }
        )
