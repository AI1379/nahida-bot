import { afterEach, describe, expect, it, vi } from "vitest";

import type { MotionDecisionRecord } from "@/domain/motionTelemetry";
import {
  activeMotionPreferences,
  appendMotionDatasetRecord,
  clearMotionDataset,
  exportMotionDataset,
  parseJsonLines,
  readMotionDataset,
  recordsToJsonLines,
} from "./motionDatasetStorage";

function fakeBrowserStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

const decision: MotionDecisionRecord = {
  schemaVersion: 1,
  type: "motion_decision",
  timestamp: "2026-08-12T00:00:00.000Z",
  decisionId: "decision-1",
  assistantText: "我先想一下。",
  runtimeStatus: "speaking",
  selectedIntent: {
    id: "intent-1",
    source: "rule",
    intent: "thinking",
    emotion: "thinking",
    durationMs: 1200,
    intensity: 0.35,
    loopable: true,
    interruptible: true,
    priority: "speech",
  },
  source: "rule",
  modelId: "nahida-1080",
  modelProfileVersion: "default-v1",
  plannerVersion: "rule-v1",
  cacheHit: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("motion dataset storage", () => {
  it("round-trips JSONL without changing records", () => {
    const records = [decision, { ...decision, decisionId: "decision-2" }];

    expect(parseJsonLines(recordsToJsonLines(records))).toEqual(records);
  });

  it("appends, exports, and clears browser-local records", async () => {
    vi.stubGlobal("window", { localStorage: fakeBrowserStorage() });

    await appendMotionDatasetRecord("decisions", decision);

    expect(await readMotionDataset("decisions")).toEqual([decision]);
    expect(parseJsonLines((await exportMotionDataset()).decisions)).toEqual([
      decision,
    ]);
    await clearMotionDataset("decisions");
    expect(await readMotionDataset("decisions")).toEqual([]);
  });

  it("resolves append-only preference retractions", () => {
    const active = activeMotionPreferences([
      {
        schemaVersion: 1,
        type: "motion_preference",
        timestamp: "2026-08-12T00:00:00.000Z",
        preferenceId: "preference-1",
        assistantText: "解释一下。",
        candidateA: "plan-1",
        labels: ["good"],
      },
      {
        schemaVersion: 1,
        type: "motion_preference_retraction",
        timestamp: "2026-08-12T00:01:00.000Z",
        retractionId: "retraction-1",
        retractsPreferenceId: "preference-1",
        motionPlanId: "plan-1",
      },
      {
        schemaVersion: 1,
        type: "motion_preference",
        timestamp: "2026-08-12T00:02:00.000Z",
        preferenceId: "preference-2",
        assistantText: "解释一下。",
        candidateA: "plan-1",
        labels: ["too_much"],
      },
    ]);

    expect(active.map((record) => record.preferenceId)).toEqual([
      "preference-2",
    ]);
  });
});
