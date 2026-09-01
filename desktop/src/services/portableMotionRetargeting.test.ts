import { describe, expect, it } from "vitest";

import type { PortableMotionAsset } from "@/domain/portableMotion";
import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";

import {
  analyzePortableMotionCompatibility,
  retargetPortableMotion,
} from "./portableMotionRetargeting";

const asset: PortableMotionAsset = {
  schemaVersion: 1,
  id: "portable-greet",
  name: "Portable greeting",
  durationMs: 1000,
  loopable: false,
  restoreAtEnd: true,
  channels: ["headYaw", "mouthOpen"],
  frames: [
    { atMs: 0, headYaw: 0, mouthOpen: 0 },
    { atMs: 1000, headYaw: 1, mouthOpen: 1 },
  ],
  features: [
    { atMs: 300, featureId: "hand.raise", value: true, durationMs: 500 },
  ],
  source: {
    format: "motion3",
    importerVersion: "test",
  },
};

function target(parameterIds: string[], supportedFeatureIds: string[] = []) {
  const profile = createDefaultModelPerformanceProfile("target-model");
  return {
    modelId: profile.modelId,
    parameterIds,
    poseParameterMap: {
      ...profile.poseParameterMap,
      headYaw: ["TargetYaw"],
      mouthOpen: ["TargetMouth"],
    },
    supportedFeatureIds,
  };
}

describe("portable motion retargeting", () => {
  it("reports partial support and emits only supported pose channels", () => {
    const result = retargetPortableMotion(
      asset,
      target(["TargetYaw"], ["hand.raise"]),
      { intentId: "intent-1" },
    );

    expect(result.compatibility).toMatchObject({
      status: "partial",
      poseCoverage: 0.5,
      featureCoverage: 1,
      supportedChannels: ["headYaw"],
      missingChannels: ["mouthOpen"],
    });
    expect(result.clip).toMatchObject({
      id: "portable-greet:target-model",
      intentId: "intent-1",
      channels: ["headYaw"],
    });
    expect(result.clip?.frames.at(-1)).toMatchObject({
      atMs: 1000,
      headYaw: 1,
      mouthOpen: 0,
    });
    expect(result.featureCues).toEqual(asset.features);
  });

  it("reports an incompatible target without producing a pose clip", () => {
    const result = retargetPortableMotion(asset, target([]));

    expect(result.compatibility.status).toBe("incompatible");
    expect(result.compatibility.poseCoverage).toBe(0);
    expect(result.compatibility.featureCoverage).toBe(0);
    expect(result.clip).toBeNull();
    expect(result.featureCues).toEqual([]);
  });

  it("reports full compatibility when every capability has a binding", () => {
    const report = analyzePortableMotionCompatibility(
      asset,
      target(["TargetYaw", "TargetMouth"], ["hand.raise"]),
    );

    expect(report.status).toBe("full");
    expect(report.poseCoverage).toBe(1);
    expect(report.featureCoverage).toBe(1);
  });
});
