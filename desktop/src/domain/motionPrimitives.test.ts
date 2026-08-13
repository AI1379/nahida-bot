import { describe, expect, it } from "vitest";

import {
  displayMotionPrimitiveMap,
  generateMotionPrimitive,
  motionPrimitiveNames,
} from "./motionPrimitives";

describe("motion primitives", () => {
  it("covers every legacy DisplayMotion with a procedural primitive", () => {
    expect(Object.keys(displayMotionPrimitiveMap).sort()).toEqual(
      [
        "idle",
        "nod",
        "point",
        "wave",
        "notify",
        "speaking",
        "emerge",
        "retreat",
      ].sort(),
    );
    expect(motionPrimitiveNames).toEqual(
      expect.arrayContaining([
        "idle-breathe",
        "blink",
        "glance",
        "glance-right",
        "shake",
        "think-loop",
        "explain-small",
        "surprised-pop",
        "sad-drop",
        "happy-bounce",
        "celebrate",
      ]),
    );
  });

  it("preserves the legacy nod profile at its reference intensity", () => {
    const clip = generateMotionPrimitive("nod", {
      clipId: "clip-1",
      intentId: "intent-1",
      durationMs: 1180,
      intensity: 0.45,
    });

    expect(clip.channels).toEqual(["headPitch", "bodyPitch"]);
    expect(clip.frames[1]).toMatchObject({
      atMs: 236,
      headPitch: 0.4,
      bodyPitch: 0.12,
    });
    expect(clip.frames.at(-1)).toMatchObject({
      atMs: 1180,
      headPitch: 0,
      bodyPitch: 0,
    });
  });

  it("scales intensity, repeats inside the requested duration, and restores start pose", () => {
    const clip = generateMotionPrimitive("idle-breathe", {
      clipId: "clip-2",
      intentId: "intent-2",
      durationMs: 3000,
      intensity: 0.1,
      repeat: 3,
      startPose: { headPitch: 0.2, breath: 0.1 },
    });

    expect(clip.frames).toHaveLength(7);
    expect(clip.frames[1]?.atMs).toBe(500);
    expect(clip.frames[1]?.headPitch).toBeCloseTo(0.215);
    expect(clip.frames[1]?.breath).toBeCloseTo(0.6);
    expect(clip.frames.at(-1)).toMatchObject({
      atMs: 3000,
      headPitch: 0.2,
      breath: 0.1,
    });
  });

  it("clamps generated channel values to canonical ranges", () => {
    const clip = generateMotionPrimitive("nod", {
      clipId: "clip-3",
      intentId: "intent-3",
      intensity: 1,
      startPose: { headPitch: 0.9 },
    });

    expect(Math.max(...clip.frames.map((frame) => frame.headPitch))).toBe(1);
  });
});
