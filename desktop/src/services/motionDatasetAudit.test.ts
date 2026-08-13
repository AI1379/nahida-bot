import { describe, expect, it } from "vitest";

import { auditMotionDataset } from "./motionDatasetAudit";

const timestamp = "2026-08-12T00:00:00.000Z";

function decision(
  decisionId: string,
  intent = "explain",
  playbackSurface?: "pet" | "workbench" | "debug",
) {
  return {
    schemaVersion: 1,
    type: "motion_decision",
    timestamp,
    decisionId,
    assistantText: "说明一下。",
    runtimeStatus: "speaking",
    selectedIntent: { intent },
    source: "rule",
    modelId: "test-model",
    modelProfileVersion: "default-v1",
    plannerVersion: "rule-v1",
    cacheHit: false,
    playbackSurface,
  };
}

function execution(
  decisionId: string,
  playbackSurface?: "pet" | "workbench" | "debug",
) {
  return {
    schemaVersion: 1,
    type: "motion_execution",
    timestamp,
    decisionId,
    motionPlanId: `${decisionId}-plan`,
    intent: { intent: "explain" },
    modelId: "test-model",
    modelProfileVersion: "default-v1",
    driverVersion: "rule-v1",
    synthesizerVersion: "primitive-v1",
    validatorVersion: "validator-v1",
    mixerVersion: "mixer-v1",
    primitive: "explain-small",
    durationMs: 1000,
    frameCount: 4,
    validationStatus: "accepted",
    validationWarnings: [],
    fallbackUsed: false,
    motionPlan: { id: `${decisionId}-plan` },
    normalizedClip: { id: `${decisionId}-clip` },
    playbackSurface,
  };
}

describe("auditMotionDataset", () => {
  it("detects malformed records and orphan executions", () => {
    const report = auditMotionDataset({
      decisions: [decision("decision-1"), { schemaVersion: 2 }],
      executions: [execution("missing-decision")],
    });

    expect(report.validRecordRatio).toBe(2 / 3);
    expect(report.executionLinkageRatio).toBe(0);
    expect(report.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: "decisions", severity: "error" }),
      expect.objectContaining({ kind: "executions", severity: "warning" }),
    ]));
    expect(report.readyForTraining).toBe(false);
  });

  it("reports training readiness for a linked and sufficiently covered set", () => {
    const intents = [
      "idle",
      "greet",
      "thinking",
      "explain",
      "agree",
      "deny",
      "surprised",
      "concerned",
    ];
    const decisions = Array.from({ length: 500 }, (_, index) =>
      decision(`decision-${index}`, intents[index % intents.length]),
    );
    const executions = decisions.map((record) => execution(record.decisionId));
    const preferences = Array.from({ length: 100 }, (_, index) => ({
      schemaVersion: 1,
      type: "motion_preference",
      timestamp,
      preferenceId: `preference-${index}`,
      assistantText: "说明一下。",
      candidateA: `decision-${index}-plan`,
      labels: ["good"],
    }));

    const report = auditMotionDataset({ decisions, executions, preferences });

    expect(report.readyForTraining).toBe(true);
    expect(report.criteria.every((criterion) => criterion.passed)).toBe(true);
    expect(report.distinctIntentCount).toBe(8);
  });

  it("does not count retracted preferences toward readiness", () => {
    const report = auditMotionDataset({
      decisions: [decision("decision-1")],
      executions: [execution("decision-1")],
      preferences: [
        {
          schemaVersion: 1,
          type: "motion_preference",
          timestamp,
          preferenceId: "preference-1",
          assistantText: "说明一下。",
          candidateA: "decision-1-plan",
          labels: ["good"],
        },
        {
          schemaVersion: 1,
          type: "motion_preference_retraction",
          timestamp,
          retractionId: "retraction-1",
          retractsPreferenceId: "preference-1",
          motionPlanId: "decision-1-plan",
        },
      ],
    });

    expect(report.counts.preferences).toBe(0);
    expect(
      report.criteria.find((criterion) => criterion.id === "preference-volume")
        ?.current,
    ).toBe(0);
  });

  it("excludes Workbench and debug previews from training readiness", () => {
    const report = auditMotionDataset({
      decisions: [
        decision("pet-decision", "explain", "pet"),
        decision("preview-decision", "greet", "workbench"),
      ],
      executions: [
        execution("pet-decision", "pet"),
        execution("preview-decision", "workbench"),
      ],
      preferences: [
        {
          schemaVersion: 1,
          type: "motion_preference",
          timestamp,
          preferenceId: "preview-preference",
          assistantText: "预览。",
          candidateA: "preview-decision-plan",
          labels: ["good"],
          playbackSurface: "workbench",
        },
      ],
    });

    expect(report.counts.decisions).toBe(1);
    expect(report.counts.executions).toBe(1);
    expect(report.counts.preferences).toBe(0);
    expect(report.distinctIntentCount).toBe(1);
  });
});
