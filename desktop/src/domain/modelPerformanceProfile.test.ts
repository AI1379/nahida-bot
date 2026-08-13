import { describe, expect, it } from "vitest";

import {
  createDefaultModelPerformanceProfile,
  sanitizeModelPerformanceProfile,
  withLocalModelPerformanceProfileVersion,
} from "./modelPerformanceProfile";

describe("model performance profile", () => {
  it("provides canonical parameter maps and dynamics limits", () => {
    const profile = createDefaultModelPerformanceProfile("nahida-1080");

    expect(profile.modelId).toBe("nahida-1080");
    expect(profile.poseParameterMap.headYaw).toContain("ParamAngleX");
    expect(profile.maxVelocity.headYaw).toBeGreaterThan(0);
    expect(profile.maxAcceleration.headYaw).toBeGreaterThan(
      profile.maxVelocity.headYaw ?? 0,
    );
  });

  it("sanitizes calibration values and preserves missing defaults", () => {
    const fallback = createDefaultModelPerformanceProfile("test");
    const profile = sanitizeModelPerformanceProfile(
      {
        profileVersion: "calibrated-v2",
        intensityScale: 4,
        preferredIdleEnergy: -1,
        poseParameterMap: { headYaw: [" CustomYaw ", "CustomYaw"] },
        maxVelocity: { headYaw: 0.8, bodyYaw: -2 },
        forbiddenCombos: [
          { primitive: "happy-bounce", reason: " too cheerful " },
          { primitive: "bad" },
        ],
      },
      fallback,
    );

    expect(profile).toMatchObject({
      modelId: "test",
      profileVersion: "calibrated-v2",
      intensityScale: 2,
      preferredIdleEnergy: 0,
      maxVelocity: { headYaw: 0.8 },
    });
    expect(profile.poseParameterMap.headYaw).toEqual(["CustomYaw"]);
    expect(profile.poseParameterMap.mouthOpen).toEqual(
      fallback.poseParameterMap.mouthOpen,
    );
    expect(profile.forbiddenCombos).toEqual([
      {
        expression: undefined,
        primitive: "happy-bounce",
        reason: "too cheerful",
      },
    ]);
  });

  it("derives a stable profile version from local calibration content", () => {
    const first = createDefaultModelPerformanceProfile("test");
    const unchanged = withLocalModelPerformanceProfileVersion(first);
    const repeated = withLocalModelPerformanceProfileVersion({ ...first });
    const changed = withLocalModelPerformanceProfileVersion({
      ...first,
      intensityScale: 1.25,
    });

    expect(unchanged.profileVersion).toMatch(/^local-[0-9a-f]{8}$/u);
    expect(repeated.profileVersion).toBe(unchanged.profileVersion);
    expect(changed.profileVersion).not.toBe(unchanged.profileVersion);
  });
});
