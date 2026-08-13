"""Prepare exported Desktop motion telemetry for model training.

The script performs the last deterministic step before fitting a neural model:
it validates and joins the four local JSONL streams, creates stable dataset
splits, and writes planner, curve, preference, and feedback datasets plus a
manifest. It does not train a model or upload private assistant text.

Usage::

    python -m scripts.prepare_motion_training_data \
        --input-dir path/to/exported-jsonl \
        --output-dir data/motion-training-prepared
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

DATASET_KINDS = ("decisions", "executions", "preferences", "invalid")
EXPECTED_TYPES = {
    "decisions": "motion_decision",
    "executions": "motion_execution",
    "preferences": "motion_preference",
    "invalid": "motion_invalid",
}
PREFERENCE_TYPES = {"motion_preference", "motion_preference_retraction"}
EXCLUDED_PLAYBACK_SURFACES = {"workbench", "debug"}
SPLIT_NAMES = ("train", "validation", "test")


class DatasetPreparationError(ValueError):
    """Raised when exported telemetry cannot safely become training data."""


def _source_files(input_dir: Path, kind: str) -> list[Path]:
    exact = input_dir / f"{kind}.jsonl"
    if exact.is_file():
        return [exact]
    return sorted(input_dir.glob(f"{kind}-*.jsonl"))


def read_exported_records(input_dir: Path, kind: str) -> list[dict[str, Any]]:
    """Read one record kind from either canonical or date-suffixed exports."""
    if kind not in DATASET_KINDS:
        raise DatasetPreparationError(f"Unsupported dataset kind: {kind}")
    records: list[dict[str, Any]] = []
    for path in _source_files(input_dir, kind):
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise DatasetPreparationError(
                        f"Invalid JSON at {path}:{line_number}: {error}"
                    ) from error
                if not isinstance(record, dict):
                    raise DatasetPreparationError(
                        f"Expected a JSON object at {path}:{line_number}"
                    )
                if record.get("schemaVersion") != 1:
                    raise DatasetPreparationError(
                        f"Unsupported schema at {path}:{line_number}"
                    )
                record_type = record.get("type")
                type_is_valid = (
                    record_type in PREFERENCE_TYPES
                    if kind == "preferences"
                    else record_type == EXPECTED_TYPES[kind]
                )
                if not type_is_valid:
                    raise DatasetPreparationError(
                        f"Unexpected record type at {path}:{line_number}"
                    )
                records.append(record)
    return records


def stable_split(sample_id: str) -> str:
    """Assign an id to an 80/10/10 split without depending on input order."""
    bucket = int(hashlib.sha256(sample_id.encode("utf-8")).hexdigest()[:8], 16)
    percentile = bucket % 100
    if percentile < 80:
        return "train"
    if percentile < 90:
        return "validation"
    return "test"


def _unique_by(records: Iterable[dict[str, Any]], key: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for record in records:
        value = record.get(key)
        if not isinstance(value, str) or not value:
            raise DatasetPreparationError(f"Record is missing {key}")
        if value in indexed:
            raise DatasetPreparationError(f"Duplicate {key}: {value}")
        indexed[value] = record
    return indexed


def _index_executions(
    decisions: dict[str, dict], executions: Iterable[dict]
) -> tuple[dict[str, list[dict]], dict[str, dict], list[str]]:
    executions_by_decision: dict[str, list[dict]] = {}
    plans: dict[str, dict] = {}
    orphan_execution_ids: list[str] = []
    for execution in executions:
        if execution.get("playbackSurface") in EXCLUDED_PLAYBACK_SURFACES:
            continue
        decision_id = execution.get("decisionId")
        plan_id = execution.get("motionPlanId")
        if not isinstance(decision_id, str) or decision_id not in decisions:
            orphan_execution_ids.append(str(decision_id))
            continue
        if not isinstance(plan_id, str) or not plan_id:
            raise DatasetPreparationError("Execution is missing motionPlanId")
        executions_by_decision.setdefault(decision_id, []).append(execution)
        plans[plan_id] = execution
    return executions_by_decision, plans, orphan_execution_ids


def _planner_sample(
    decision_id: str,
    decision: dict,
    execution: dict,
    preference: dict | None,
) -> dict | None:
    target = decision.get("selectedIntent")
    if not isinstance(target, dict) or not isinstance(target.get("intent"), str):
        raise DatasetPreparationError(
            f"Decision {decision_id} has no structured selectedIntent"
        )
    target = dict(target)
    labels = preference.get("labels", []) if preference else []
    correction = preference.get("correction") if preference else None
    if not isinstance(correction, dict):
        correction = {}
    if any(label in {"bad", "wrong_emotion"} for label in labels) and not correction:
        return None
    for field in ("intent", "emotion", "intensity"):
        if correction.get(field) is not None:
            target[field] = correction[field]
    return {
        "sampleId": decision_id,
        "input": {
            "assistantText": decision.get("assistantText", ""),
            "runtimeStatus": decision.get("runtimeStatus"),
            "modelId": decision.get("modelId"),
            "modelProfileVersion": decision.get("modelProfileVersion"),
        },
        "target": target,
        "metadata": {
            "plannerVersion": decision.get("plannerVersion"),
            "source": decision.get("source"),
            "cacheHit": decision.get("cacheHit", False),
            "primitive": execution.get("primitive"),
            "validationStatus": execution.get("validationStatus"),
            "fallbackUsed": execution.get("fallbackUsed", False),
            "humanReviewed": preference is not None,
            "feedbackLabels": labels,
            "humanCorrected": bool(correction),
        },
    }


def _curve_sample(decision_id: str, execution: dict) -> dict:
    motion_plan = execution.get("motionPlan")
    normalized_clip = execution.get("normalizedClip")
    if not isinstance(motion_plan, dict) or not isinstance(normalized_clip, dict):
        raise DatasetPreparationError(
            f"Execution for {decision_id} has no plan or normalized clip"
        )
    return {
        "sampleId": execution.get("motionPlanId"),
        "intent": execution.get("intent"),
        "motionPlan": motion_plan,
        "normalizedClip": normalized_clip,
        "modelId": execution.get("modelId"),
        "modelProfileVersion": execution.get("modelProfileVersion"),
        "primitive": execution.get("primitive"),
        "validationWarnings": execution.get("validationWarnings", []),
        "fallbackUsed": False,
    }


def _training_samples(
    decisions: dict[str, dict],
    executions_by_decision: dict[str, list[dict]],
    preferences_by_plan: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    planner_samples: list[dict] = []
    curve_samples: list[dict] = []
    for decision_id, decision in decisions.items():
        linked = executions_by_decision.get(decision_id, [])
        if not linked:
            continue
        execution = linked[-1]
        preference = preferences_by_plan.get(str(execution.get("motionPlanId")))
        planner_sample = _planner_sample(decision_id, decision, execution, preference)
        if planner_sample is not None:
            planner_samples.append(planner_sample)
        negative_labels = set(preference.get("labels", [])) if preference else set()
        if not execution.get(
            "fallbackUsed", False
        ) and not negative_labels.intersection(
            {"bad", "too_much", "too_little", "wrong_emotion", "repetitive"}
        ):
            curve_samples.append(_curve_sample(decision_id, execution))
    return planner_samples, curve_samples


def _preference_samples(
    preferences: Iterable[dict], plans: dict[str, dict]
) -> tuple[list[dict], list[dict], list[str]]:
    paired_preferences: list[dict] = []
    labelled_feedback: list[dict] = []
    orphan_preference_ids: list[str] = []
    for preference in preferences:
        candidate_a = preference.get("candidateA")
        candidate_b = preference.get("candidateB")
        candidates = [candidate_a, candidate_b] if candidate_b else [candidate_a]
        if any(
            not isinstance(candidate, str) or candidate not in plans
            for candidate in candidates
        ):
            orphan_preference_ids.append(str(preference.get("preferenceId")))
            continue
        if candidate_b and preference.get("winner") in candidates:
            paired_preferences.append(preference)
        else:
            labelled_feedback.append(preference)
    paired_preferences.sort(key=lambda sample: str(sample.get("preferenceId")))
    labelled_feedback.sort(key=lambda sample: str(sample.get("preferenceId")))
    return paired_preferences, labelled_feedback, orphan_preference_ids


def _active_preferences(records: Iterable[dict]) -> list[dict]:
    records = list(records)
    retracted_ids = {
        str(record.get("retractsPreferenceId"))
        for record in records
        if record.get("type") == "motion_preference_retraction"
    }
    return [
        record
        for record in records
        if record.get("type") == "motion_preference"
        and str(record.get("preferenceId")) not in retracted_ids
    ]


def _split_samples(samples: list[dict]) -> dict[str, list[dict]]:
    return {
        name: [
            sample
            for sample in samples
            if stable_split(str(sample["sampleId"])) == name
        ]
        for name in SPLIT_NAMES
    }


def prepare_datasets(records: dict[str, list[dict]]) -> dict[str, Any]:
    """Join telemetry and return serializable, deterministic training sets."""
    decisions = _unique_by(records.get("decisions", []), "decisionId")
    executions_by_decision, plans, orphan_execution_ids = _index_executions(
        decisions, records.get("executions", [])
    )
    active_preferences = _active_preferences(records.get("preferences", []))
    preferences_by_plan = {
        str(preference.get("candidateA")): preference
        for preference in sorted(
            active_preferences, key=lambda item: str(item.get("timestamp", ""))
        )
    }
    planner_samples, curve_samples = _training_samples(
        decisions, executions_by_decision, preferences_by_plan
    )
    paired_preferences, labelled_feedback, orphan_preference_ids = _preference_samples(
        active_preferences, plans
    )
    planner_samples.sort(key=lambda sample: sample["sampleId"])
    curve_samples.sort(key=lambda sample: str(sample["sampleId"]))
    intent_counts = Counter(
        str(sample["target"]["intent"]) for sample in planner_samples
    )

    return {
        "planner": _split_samples(planner_samples),
        "curves": _split_samples(curve_samples),
        "preferences": paired_preferences,
        "feedback": labelled_feedback,
        "manifest": {
            "schemaVersion": 1,
            "sourceCounts": {
                kind: len(records.get(kind, [])) for kind in DATASET_KINDS
            },
            "plannerSampleCount": len(planner_samples),
            "curveSampleCount": len(curve_samples),
            "pairedPreferenceCount": len(paired_preferences),
            "labelledFeedbackCount": len(labelled_feedback),
            "correctedFeedbackCount": sum(
                1 for item in labelled_feedback if item.get("correction")
            ),
            "retractedPreferenceCount": sum(
                1
                for item in records.get("preferences", [])
                if item.get("type") == "motion_preference_retraction"
            ),
            "intentCounts": dict(sorted(intent_counts.items())),
            "orphanExecutionIds": orphan_execution_ids,
            "orphanPreferenceIds": orphan_preference_ids,
            "readyForPlannerTraining": (
                len(planner_samples) >= 500 and len(intent_counts) >= 8
            ),
            "readyForPreferenceTraining": len(paired_preferences) >= 100,
            "splitStrategy": "sha256-id-80-10-10-v1",
        },
    }


def _write_jsonl(path: Path, records: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def write_prepared_datasets(output_dir: Path, prepared: dict[str, Any]) -> None:
    """Write only this tool's well-known files into the target directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for family in ("planner", "curves"):
        for split in SPLIT_NAMES:
            _write_jsonl(
                output_dir / f"{family}-{split}.jsonl",
                prepared[family][split],
            )
    _write_jsonl(output_dir / "preferences.jsonl", prepared["preferences"])
    _write_jsonl(output_dir / "feedback.jsonl", prepared["feedback"])
    (output_dir / "manifest.json").write_text(
        json.dumps(prepared["manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_from_directories(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Read an export directory, prepare all datasets, and persist the result."""
    if not input_dir.is_dir():
        raise DatasetPreparationError(f"Input directory does not exist: {input_dir}")
    records = {kind: read_exported_records(input_dir, kind) for kind in DATASET_KINDS}
    prepared = prepare_datasets(records)
    write_prepared_datasets(output_dir, prepared)
    return prepared["manifest"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = prepare_from_directories(args.input_dir, args.output_dir)
    except DatasetPreparationError as error:
        parser.error(str(error))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
