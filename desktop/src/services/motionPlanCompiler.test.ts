import { describe, expect, it } from "vitest";

import type { MotionPlan } from "@/domain/motionPlan";
import { compileMotionPlan } from "./motionPlanCompiler";

function plan(segments: MotionPlan["segments"]): MotionPlan {
  return {
    schemaVersion: 1,
    id: "plan-1",
    createdAt: "2026-08-12T00:00:00.000Z",
    durationMs: 1200,
    intent: {
      id: "intent-1",
      source: "rule",
      intent: "agree",
      emotion: "neutral",
      durationMs: 1200,
      intensity: 0.5,
      loopable: false,
      interruptible: true,
      priority: "speech",
    },
    segments,
    validationWarnings: [],
  };
}

describe("compileMotionPlan", () => {
  it("compiles primitive segments into a normalized timeline", () => {
    const clip = compileMotionPlan(plan([
      {
        type: "primitive",
        name: "nod",
        atMs: 0,
        durationMs: 1200,
        params: { intensity: 0.5, repeat: 1 },
      },
    ]));

    expect(clip).toMatchObject({
      id: "plan-1:compiled",
      intentId: "intent-1",
      durationMs: 1200,
      channels: ["headPitch", "bodyPitch"],
    });
    expect(clip?.frames[0]?.atMs).toBe(0);
    expect(clip?.frames.at(-1)?.atMs).toBe(1200);
  });

  it("ignores expression-only plans at the clip boundary", () => {
    expect(compileMotionPlan(plan([
      { type: "expression", atMs: 0, expressionKey: "happy" },
    ]))).toBeNull();
  });
});
