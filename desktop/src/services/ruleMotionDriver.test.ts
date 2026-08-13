import { describe, expect, it } from "vitest";

import { createMotionDriverInput } from "@/domain/motionDriver";
import type { MotionIntent } from "@/domain/motionIntent";
import { RuleMotionDriver } from "./ruleMotionDriver";

function intent(overrides: Partial<MotionIntent> = {}): MotionIntent {
  return {
    id: "intent-1",
    source: "rule",
    intent: "explain",
    emotion: "neutral",
    durationMs: 1200,
    intensity: 0.35,
    loopable: false,
    interruptible: true,
    priority: "speech",
    ...overrides,
  };
}

describe("RuleMotionDriver", () => {
  it("selects a primitive from semantic intent", async () => {
    const driver = new RuleMotionDriver();
    const result = await driver.drive(
      createMotionDriverInput({
        intent: intent({ intent: "deny", intensity: 0.5 }),
        context: { runtimeStatus: "speaking" },
      }),
    );

    expect(result.clip?.id).toBe("intent-1:shake");
    expect(result.clip?.channels).toContain("headYaw");
    expect(result.warnings).toEqual([]);
  });

  it("honors a compatibility motion hint over semantic selection", async () => {
    const driver = new RuleMotionDriver();
    const result = await driver.drive(
      createMotionDriverInput({
        intent: intent(),
        context: { runtimeStatus: "speaking", motionHint: "point" },
      }),
    );

    expect(result.clip?.id).toBe("intent-1:point");
    expect(result.clip?.channels).toContain("headYaw");
  });

  it("uses audio energy to strengthen speaking motion", async () => {
    const driver = new RuleMotionDriver();
    const quiet = await driver.drive(
      createMotionDriverInput({
        intent: intent(),
        audioEnergy: 0,
        context: { runtimeStatus: "speaking", motionHint: "speaking" },
      }),
    );
    const energetic = await driver.drive(
      createMotionDriverInput({
        intent: intent(),
        audioEnergy: 1,
        context: { runtimeStatus: "speaking", motionHint: "speaking" },
      }),
    );

    expect(Math.abs(energetic.clip?.frames[1]?.headPitch ?? 0)).toBeGreaterThan(
      Math.abs(quiet.clip?.frames[1]?.headPitch ?? 0),
    );
  });
});
