import { describe, expect, it } from "vitest";

import type { MotionIntent } from "./motionIntent";
import { createMotionDriverInput, motionDriverDefaults } from "./motionDriver";

const intent: MotionIntent = {
  id: "presentation-1:segment:0",
  source: "rule",
  intent: "explain",
  emotion: "neutral",
  durationMs: 1200,
  intensity: 0.35,
  loopable: false,
  interruptible: true,
  priority: "speech",
};

describe("createMotionDriverInput", () => {
  it("fills stable driver defaults and a neutral previous pose", () => {
    const input = createMotionDriverInput({
      intent,
      context: { runtimeStatus: "speaking" },
    });

    expect(input.phase).toBe(motionDriverDefaults.phase);
    expect(input.lookaheadMs).toBe(motionDriverDefaults.lookaheadMs);
    expect(input.audioEnergy).toBe(0);
    expect(input.previousPose).toMatchObject({
      headYaw: 0,
      eyeOpenLeft: 1,
      eyeOpenRight: 1,
    });
  });

  it("clamps realtime input while preserving pose overrides", () => {
    const input = createMotionDriverInput({
      intent,
      phase: "sustain",
      previousPose: { headYaw: -0.4 },
      audioEnergy: 2,
      lookaheadMs: -20,
      context: { runtimeStatus: "speaking", modelId: "nahida-1080" },
    });

    expect(input).toMatchObject({
      phase: "sustain",
      audioEnergy: 1,
      lookaheadMs: 1,
      previousPose: { headYaw: -0.4, eyeOpenLeft: 1 },
      context: { modelId: "nahida-1080" },
    });
  });
});
