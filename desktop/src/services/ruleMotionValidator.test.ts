import { describe, expect, it } from "vitest";

import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import { generateMotionPrimitive } from "@/domain/motionPrimitives";
import { createNormalizedPoseFrame } from "@/domain/normalizedPose";
import { RuleMotionValidator } from "./ruleMotionValidator";

describe("RuleMotionValidator", () => {
  it("accepts a generated primitive that fits the default profile", () => {
    const validator = new RuleMotionValidator();
    const clip = generateMotionPrimitive("nod", {
      clipId: "clip-1",
      intentId: "intent-1",
    });

    const result = validator.validate(clip, {
      modelProfile: createDefaultModelPerformanceProfile("nahida-1080"),
      primitive: "nod",
    });

    expect(result.status).toBe("accepted");
    expect(result.clip).toEqual(clip);
    expect(result.warnings).toEqual([]);
  });

  it("corrects timestamps, non-finite values, and canonical range violations", () => {
    const validator = new RuleMotionValidator();
    const result = validator.validate(
      {
        id: "clip-2",
        intentId: "intent-2",
        durationMs: 1000,
        loopable: false,
        restoreAtEnd: true,
        channels: ["headYaw", "mouthOpen"],
        frames: [
          createNormalizedPoseFrame(800, { headYaw: 2, mouthOpen: Number.NaN }),
          createNormalizedPoseFrame(-20, { headYaw: 0 }),
        ],
      },
      { modelProfile: createDefaultModelPerformanceProfile("test") },
    );

    expect(result.status).toBe("corrected");
    expect(result.clip?.frames[0]?.atMs).toBe(0);
    expect(result.clip?.frames.at(-1)?.atMs).toBe(1000);
    expect(result.clip?.frames.every((frame) => frame.headYaw <= 1)).toBe(true);
    expect(result.clip?.frames.every((frame) => Number.isFinite(frame.mouthOpen))).toBe(
      true,
    );
    expect(result.warnings.map((warning) => warning.code)).toEqual(
      expect.arrayContaining([
        "invalid_timestamp",
        "channel_out_of_range",
        "missing_end_frame",
      ]),
    );
  });

  it("limits channel dynamics with model-specific velocity bounds", () => {
    const validator = new RuleMotionValidator();
    const profile = createDefaultModelPerformanceProfile("slow-model");
    profile.maxVelocity.headYaw = 0.5;
    profile.maxAcceleration.headYaw = 100;

    const result = validator.validate(
      {
        id: "clip-3",
        intentId: "intent-3",
        durationMs: 200,
        loopable: false,
        restoreAtEnd: true,
        channels: ["headYaw"],
        frames: [
          createNormalizedPoseFrame(0),
          createNormalizedPoseFrame(100, { headYaw: 0.8 }),
          createNormalizedPoseFrame(200),
        ],
      },
      { modelProfile: profile },
    );

    expect(result.status).toBe("corrected");
    expect(result.clip?.frames[1]?.headYaw).toBeCloseTo(0.05);
    expect(result.warnings[0]?.code).toBe("dynamics_limited");
  });

  it("rejects forbidden primitive and expression combinations", () => {
    const validator = new RuleMotionValidator();
    const profile = createDefaultModelPerformanceProfile("test");
    profile.forbiddenCombos.push({
      primitive: "happy-bounce",
      expression: "error",
      reason: "Character profile forbids cheerful error motion.",
    });
    const clip = generateMotionPrimitive("happy-bounce", {
      clipId: "clip-4",
      intentId: "intent-4",
    });

    const result = validator.validate(clip, {
      modelProfile: profile,
      primitive: "happy-bounce",
      expression: "error",
    });

    expect(result.status).toBe("rejected");
    expect(result.clip).toBeNull();
    expect(result.warnings[0]?.code).toBe("forbidden_combo");
  });
});
