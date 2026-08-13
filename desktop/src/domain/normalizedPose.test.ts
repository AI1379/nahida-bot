import { describe, expect, it } from "vitest";

import {
  createNormalizedPoseFrame,
  neutralNormalizedPose,
  normalizedPoseChannelRanges,
  normalizedPoseChannels,
} from "./normalizedPose";

describe("normalized pose", () => {
  it("creates a complete neutral frame", () => {
    const frame = createNormalizedPoseFrame(120);

    expect(frame).toEqual({ atMs: 120, ...neutralNormalizedPose });
    expect(normalizedPoseChannels.every((channel) => channel in frame)).toBe(
      true,
    );
  });

  it("applies channel overrides without mutating the neutral pose", () => {
    const frame = createNormalizedPoseFrame(80, {
      headYaw: 0.4,
      mouthOpen: 0.25,
    });

    expect(frame).toMatchObject({
      atMs: 80,
      headYaw: 0.4,
      mouthOpen: 0.25,
      eyeOpenLeft: 1,
    });
    expect(neutralNormalizedPose.headYaw).toBe(0);
  });

  it("defines a range for every canonical channel", () => {
    expect(Object.keys(normalizedPoseChannelRanges).sort()).toEqual(
      [...normalizedPoseChannels].sort(),
    );
    expect(normalizedPoseChannelRanges.headYaw).toEqual({
      minimum: -1,
      maximum: 1,
    });
    expect(normalizedPoseChannelRanges.mouthOpen).toEqual({
      minimum: 0,
      maximum: 1,
    });
  });
});
