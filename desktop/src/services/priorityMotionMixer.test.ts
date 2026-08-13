import { describe, expect, it } from "vitest";

import type { MotionLayer } from "@/domain/motionRuntime";
import {
  createNormalizedPoseFrame,
  type NormalizedMotionClip,
  type NormalizedPoseChannel,
} from "@/domain/normalizedPose";
import { PriorityMotionMixer } from "./priorityMotionMixer";

function clip(
  id: string,
  channel: NormalizedPoseChannel,
  value: number,
): NormalizedMotionClip {
  return {
    id,
    intentId: `${id}-intent`,
    durationMs: 1000,
    loopable: false,
    restoreAtEnd: true,
    channels: [channel],
    frames: [
      createNormalizedPoseFrame(0),
      createNormalizedPoseFrame(500, { [channel]: value }),
      createNormalizedPoseFrame(1000),
    ],
  };
}

function layer(
  id: string,
  source: MotionLayer["source"],
  motionClip: NormalizedMotionClip,
  sequence = 1,
): MotionLayer {
  return { id, source, sequence, clip: motionClip };
}

describe("PriorityMotionMixer", () => {
  it("uses the highest-priority layer independently for each channel", () => {
    const mixer = new PriorityMotionMixer();
    const result = mixer.mix([
      layer("idle-head", "idle", clip("idle-head", "headYaw", 0.1)),
      layer("speech-head", "speech", clip("speech-head", "headYaw", 0.5)),
      layer("speech-mouth", "speech", clip("speech-mouth", "mouthOpen", 0.2)),
      layer("lip", "lip-sync", clip("lip", "mouthOpen", 0.8)),
    ]);

    expect(result?.channels).toEqual(["headYaw", "mouthOpen"]);
    expect(result?.frames.find((frame) => frame.atMs === 500)).toMatchObject({
      headYaw: 0.5,
      mouthOpen: 0.8,
    });
  });

  it("uses the newest layer when priorities are equal", () => {
    const mixer = new PriorityMotionMixer();
    const result = mixer.mix([
      layer("old", "speech", clip("old", "headPitch", 0.2), 1),
      layer("new", "speech", clip("new", "headPitch", 0.6), 2),
    ]);

    expect(result?.frames.find((frame) => frame.atMs === 500)?.headPitch).toBe(
      0.6,
    );
  });

  it("returns null when no layer has a usable clip", () => {
    expect(new PriorityMotionMixer().mix([])).toBeNull();
  });
});
