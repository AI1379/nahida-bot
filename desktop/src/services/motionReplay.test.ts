import { describe, expect, it } from "vitest";

import { generateMotionPrimitive } from "@/domain/motionPrimitives";
import { replayMotionClip } from "./motionReplay";

describe("replayMotionClip", () => {
  it("produces deterministic fixed-rate frames and dynamics metrics", () => {
    const clip = generateMotionPrimitive("nod", {
      clipId: "clip-1",
      intentId: "intent-1",
      durationMs: 1000,
    });

    const first = replayMotionClip(clip, 20);
    const second = replayMotionClip(clip, 20);

    expect(first).toEqual(second);
    expect(first.frames).toHaveLength(21);
    expect(first.frames.at(-1)?.atMs).toBe(1000);
    expect(first.metrics.outOfRangeCount).toBe(0);
    expect(first.metrics.channels.headPitch?.maximumVelocity).toBeGreaterThan(0);
    expect(first.metrics.channels.headPitch?.maximumAcceleration).toBeGreaterThan(
      0,
    );
  });
});
