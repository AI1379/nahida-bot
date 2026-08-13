import { describe, expect, it } from "vitest";

import { createNormalizedPoseFrame } from "@/domain/normalizedPose";

import {
  createRuntimeParameterOverride,
  groupNormalizedClipFrames,
  runtimeParameterValueAt,
} from "./live2dProceduralMotion";

const clip = {
  id: "clip-1",
  intentId: "intent-1",
  durationMs: 1000,
  loopable: false,
  restoreAtEnd: true,
  channels: ["headYaw", "mouthOpen"],
  frames: [
    createNormalizedPoseFrame(0),
    createNormalizedPoseFrame(500, { headYaw: 0.5, mouthOpen: 0.75 }),
    createNormalizedPoseFrame(1000),
  ],
} as const;

describe("live2dProceduralMotion", () => {
  it("groups normalized frames by active canonical channel", () => {
    const grouped = groupNormalizedClipFrames({
      ...clip,
      channels: [...clip.channels],
      frames: [...clip.frames],
    });

    expect(grouped.get("headYaw")).toEqual([
      { atMs: 0, value: 0 },
      { atMs: 500, value: 0.5 },
      { atMs: 1000, value: 0 },
    ]);
    expect(grouped.has("bodyYaw")).toBe(false);
  });

  it("retargets normalized values and interpolates runtime overrides", () => {
    const override = createRuntimeParameterOverride({
      clip: {
        ...clip,
        channels: [...clip.channels],
        frames: [...clip.frames],
      },
      channel: "headYaw",
      current: 0,
      range: { minimum: -10, maximum: 10, defaultValue: 0 },
      startedAt: 123,
    });

    expect(override).toMatchObject({
      original: 0,
      finalValue: 0,
      startedAt: 123,
      durationMs: 1000,
      loopable: false,
      keyframes: [
        { atMs: 0, value: 0 },
        { atMs: 500, value: 5 },
        { atMs: 1000, value: 0 },
      ],
    });
    expect(runtimeParameterValueAt(override, 250)).toBeCloseTo(2.5);
    expect(runtimeParameterValueAt(override, 500)).toBeCloseTo(5);
    expect(runtimeParameterValueAt(override, 1000)).toBeCloseTo(0);
  });

  it("keeps the root value when an in-flight motion is interrupted", () => {
    const override = createRuntimeParameterOverride({
      clip: {
        ...clip,
        channels: [...clip.channels],
        frames: [...clip.frames],
      },
      channel: "headYaw",
      current: 3,
      original: 0,
      range: { minimum: -10, maximum: 10, defaultValue: 0 },
      startedAt: 500,
    });

    expect(override.keyframes[0]).toEqual({ atMs: 0, value: 3 });
    expect(override.keyframes.at(-1)).toEqual({ atMs: 1000, value: 0 });
    expect(override.original).toBe(0);
  });

  it("wraps elapsed time for loopable primitives", () => {
    const override = createRuntimeParameterOverride({
      clip: {
        ...clip,
        loopable: true,
        channels: [...clip.channels],
        frames: [...clip.frames],
      },
      channel: "headYaw",
      current: 0,
      range: { minimum: -10, maximum: 10, defaultValue: 0 },
      startedAt: 0,
    });

    expect(runtimeParameterValueAt(override, 1250)).toBeCloseTo(2.5);
  });
});
