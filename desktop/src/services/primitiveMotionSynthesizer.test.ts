import { describe, expect, it } from "vitest";

import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import { neutralNormalizedPose } from "@/domain/normalizedPose";
import { PrimitiveMotionSynthesizer } from "./primitiveMotionSynthesizer";

describe("PrimitiveMotionSynthesizer", () => {
  it("creates a versioned model-independent MotionPlan", async () => {
    const plan = await new PrimitiveMotionSynthesizer().synthesize(
      {
        id: "intent-1",
        source: "rule",
        intent: "thinking",
        emotion: "thinking",
        durationMs: 3600,
        intensity: 0.4,
        loopable: true,
        interruptible: true,
        priority: "speech",
      },
      {
        previousPose: neutralNormalizedPose,
        audioEnergy: 0.25,
        modelProfile: createDefaultModelPerformanceProfile("test-model"),
      },
    );

    expect(plan).toMatchObject({
      schemaVersion: 1,
      id: "intent-1:plan:think-loop",
      durationMs: 3600,
      segments: [
        {
          type: "primitive",
          name: "think-loop",
          params: { intensity: 0.4, repeat: 2, audioEnergy: 0.25 },
        },
      ],
    });
  });
});
