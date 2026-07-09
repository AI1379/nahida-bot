import { describe, expect, it } from "vitest";

import { live2dRuntimeDefaults } from "@/config/desktopRuntimeDefaults";
import type { Live2DModelManifest } from "@/domain/live2d";
import { commonLive2DParameterIds } from "@/domain/live2dBaseMotion";

import {
  lipSyncParameterIdsForManifest,
  lipSyncValueForSpeakingPulse,
  scaleLipSyncParameterValue,
} from "./live2dLipSync";

function manifestWithLipSync(parameterIds: string[]): Live2DModelManifest {
  return {
    id: "test",
    name: "Test",
    entry: "/model.model3.json",
    source: "bundled",
    layout: { scale: 1, offsetX: 0, offsetY: 0, edgeExposedPx: 160 },
    emotionMap: {},
    motionMap: {},
    lipSync: { enabled: true, parameterIds },
  };
}

describe("live2dLipSync", () => {
  it("uses manifest lip sync parameters and falls back to mouth open ids", () => {
    expect(
      lipSyncParameterIdsForManifest(manifestWithLipSync(["CustomMouth"])),
    ).toEqual(["CustomMouth"]);
    expect(lipSyncParameterIdsForManifest(manifestWithLipSync([]))).toEqual(
      commonLive2DParameterIds.mouthOpen,
    );
  });

  it("scales mouth form values without touching mouth open values", () => {
    expect(scaleLipSyncParameterValue("ParamMouthOpenY", 0.5)).toBe(0.5);
    expect(scaleLipSyncParameterValue("ParamMouthForm", 0.5)).toBe(
      0.5 * live2dRuntimeDefaults.lipSync.mouthFormScale,
    );
  });

  it("derives the speaking pulse value from runtime defaults", () => {
    expect(lipSyncValueForSpeakingPulse(0)).toBeCloseTo(
      live2dRuntimeDefaults.lipSync.minimumOpen +
        (live2dRuntimeDefaults.lipSync.openRange / 2),
    );
  });
});
