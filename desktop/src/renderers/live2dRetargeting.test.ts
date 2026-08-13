import { describe, expect, it } from "vitest";

import { normalizedPoseChannels } from "@/domain/normalizedPose";

import {
  live2dParameterIdsByPoseChannel,
  live2DValueToNormalizedPose,
  normalizedPoseValueToLive2D,
} from "./live2dRetargeting";

describe("Live2D normalized pose retargeting", () => {
  it("declares model parameter candidates for every canonical channel", () => {
    expect(Object.keys(live2dParameterIdsByPoseChannel).sort()).toEqual(
      [...normalizedPoseChannels].sort(),
    );
  });

  it("maps signed values around the model default and reverses them", () => {
    const range = { minimum: -15, maximum: 25, defaultValue: 5 };

    expect(normalizedPoseValueToLive2D("headYaw", 0.5, range)).toBe(15);
    expect(normalizedPoseValueToLive2D("headYaw", -0.5, range)).toBe(-5);
    expect(live2DValueToNormalizedPose("headYaw", 15, range)).toBeCloseTo(0.5);
    expect(live2DValueToNormalizedPose("headYaw", -5, range)).toBeCloseTo(-0.5);
  });

  it("maps unit values across the complete model range", () => {
    const range = { minimum: 0, maximum: 2, defaultValue: 0.4 };

    expect(normalizedPoseValueToLive2D("mouthOpen", 0.5, range)).toBe(1);
    expect(live2DValueToNormalizedPose("mouthOpen", 1, range)).toBe(0.5);
  });
});
