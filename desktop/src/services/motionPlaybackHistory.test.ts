import { describe, expect, it } from "vitest";

import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import { mergeRecentMotionPlaybacks } from "./motionPlaybackHistory";

function playback(
  motionPlanId: string,
  timestamp: string,
  surface: MotionPlaybackSummary["surface"] = "pet",
): MotionPlaybackSummary {
  return {
    schemaVersion: 1,
    timestamp,
    decisionId: `${motionPlanId}:decision`,
    motionPlanId,
    assistantText: `Text for ${motionPlanId}`,
    surface,
    intent: {
      id: "intent",
      source: "rule",
      intent: "explain",
      emotion: "neutral",
      durationMs: 1000,
      intensity: 0.4,
      loopable: false,
      interruptible: true,
      priority: "speech",
    },
    modelId: "test",
    primitive: "explain-small",
    validationStatus: "accepted",
    fallbackUsed: false,
    motionPlan: {} as MotionPlaybackSummary["motionPlan"],
    normalizedClip: {} as MotionPlaybackSummary["normalizedClip"],
  };
}

describe("mergeRecentMotionPlaybacks", () => {
  it("deduplicates plans, sorts newest first, and applies a limit", () => {
    const merged = mergeRecentMotionPlaybacks(
      [playback("plan-a", "2026-08-12T00:00:00Z")],
      [
        playback("plan-b", "2026-08-12T00:00:02Z"),
        playback("plan-a", "2026-08-12T00:00:01Z"),
      ],
      2,
    );

    expect(merged.map((item) => item.motionPlanId)).toEqual([
      "plan-b",
      "plan-a",
    ]);
    expect(merged[1]?.timestamp).toBe("2026-08-12T00:00:01Z");
  });

  it("excludes Workbench and debug previews from recent real usage", () => {
    const merged = mergeRecentMotionPlaybacks([], [
      playback("plan-pet", "2026-08-12T00:00:00Z"),
      playback("plan-workbench", "2026-08-12T00:00:01Z", "workbench"),
      playback("plan-debug", "2026-08-12T00:00:02Z", "debug"),
    ]);

    expect(merged.map((item) => item.motionPlanId)).toEqual(["plan-pet"]);
  });
});
