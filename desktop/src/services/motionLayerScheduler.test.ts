import { afterEach, describe, expect, it, vi } from "vitest";
import { createNormalizedPoseFrame, type NormalizedMotionClip } from "@/domain/normalizedPose";
import type { Live2DRenderer } from "@/renderers/live2dRenderer";
import { MotionLayerScheduler } from "./motionLayerScheduler";
import { PriorityMotionMixer } from "./priorityMotionMixer";

afterEach(() => vi.useRealTimers());

describe("MotionLayerScheduler", () => {
  it("keeps existing layer phase when another layer starts or expires", async () => {
    vi.useFakeTimers();
    let now = 0;
    const clips: NormalizedMotionClip[] = [];
    const renderer = { playNormalizedMotion(clip: NormalizedMotionClip) { clips.push(clip); return true; },
      clearRuntimeMotion() {} } as Live2DRenderer;
    const scheduler = new MotionLayerScheduler(new PriorityMotionMixer(), renderer, () => now);
    scheduler.play("idle", "idle", { id: "idle", intentId: "idle", durationMs: 2400,
      loopable: true, restoreAtEnd: true, channels: ["breath"], frames: [
        createNormalizedPoseFrame(0, { breath: 0 }), createNormalizedPoseFrame(1200, { breath: 1 }),
        createNormalizedPoseFrame(2400, { breath: 0 }),
      ] });
    now = 1200;
    scheduler.play("nod", "speech", { id: "nod", intentId: "nod", durationMs: 300,
      loopable: false, restoreAtEnd: true, channels: ["headPitch"],
      frames: [createNormalizedPoseFrame(0), createNormalizedPoseFrame(300)] });
    expect(clips.at(-1)?.frames[0]?.breath).toBe(1);
    now = 1500;
    await vi.advanceTimersByTimeAsync(300);
    expect(clips.at(-1)?.frames[0]?.breath).toBeCloseTo(0.75);
    scheduler.clear();
  });
});
