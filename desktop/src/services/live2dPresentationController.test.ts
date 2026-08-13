import { describe, expect, it } from "vitest";

import type { Live2DModelManifest } from "@/domain/live2d";
import type { MotionDriver } from "@/domain/motionDriver";
import type {
  MotionCache,
  MotionTelemetry,
} from "@/domain/motionRuntime";
import type {
  MotionDecisionRecord,
  MotionExecutionRecord,
  MotionInvalidRecord,
  MotionPlaybackSummary,
} from "@/domain/motionTelemetry";
import {
  neutralNormalizedPose,
  type NormalizedMotionClip,
} from "@/domain/normalizedPose";
import type {
  Live2DRenderer,
} from "@/renderers/live2dRenderer";
import { Live2DPresentationController } from "./live2dPresentationController";

class FakeRenderer implements Live2DRenderer {
  readonly expressions: string[] = [];
  readonly modelMotions: Array<[string, number]> = [];
  readonly normalizedMotions: NormalizedMotionClip[] = [];
  modelMotionResult = false;
  successfulExpression = "";

  async loadModel(): Promise<void> {}
  updateModelConfig(): void {}

  async applyExpression(expressionName: string): Promise<boolean> {
    this.expressions.push(expressionName);
    return expressionName === this.successfulExpression;
  }

  async playModelMotion(group: string, index: number): Promise<boolean> {
    this.modelMotions.push([group, index]);
    return this.modelMotionResult;
  }

  playNormalizedMotion(clip: NormalizedMotionClip): boolean {
    this.normalizedMotions.push(clip);
    return true;
  }

  getNormalizedPose() {
    return { ...neutralNormalizedPose };
  }

  clearRuntimeMotion(): void {}
  setLipSync(): void {}
  setFpsMode(): void {}
  dispose(): void {}
}

const manifest: Live2DModelManifest = {
  id: "test-model",
  name: "Test Model",
  entry: "/test.model3.json",
  source: "bundled",
  emotionMap: {
    custom: ["missing-custom"],
    happy: ["missing-happy"],
    neutral: ["neutral-expression"],
  },
  motionMap: {
    nod: { source: "model", group: "Gesture", index: 1 },
  },
  lipSync: { enabled: true, parameterIds: ["ParamMouthOpenY"] },
  layout: {
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    edgeExposedPx: 42,
  },
};

describe("Live2DPresentationController", () => {
  it("tries keyword, emotion, then neutral expression mappings", async () => {
    const renderer = new FakeRenderer();
    renderer.successfulExpression = "neutral-expression";
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest(manifest);

    const result = await controller.applyExpression("custom", "happy");

    expect(renderer.expressions).toEqual([
      "missing-custom",
      "missing-happy",
      "neutral-expression",
    ]);
    expect(result.source).toBe("neutral");
  });

  it("falls back from a failed model motion to the base profile", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest(manifest);

    const result = await controller.playMotion("nod");

    expect(renderer.modelMotions).toEqual([["Gesture", 1]]);
    expect(renderer.normalizedMotions).toHaveLength(1);
    expect(renderer.normalizedMotions[0]?.id).toContain(":nod");
    expect(renderer.normalizedMotions[0]?.channels).toEqual([
      "headPitch",
      "bodyPitch",
    ]);
    expect(result.appliedSource).toBe("procedural");
  });

  it("plays new procedural primitives through the same normalized boundary", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer);

    const applied = await controller.playPrimitive("happy-bounce");

    expect(applied).toBe(true);
    expect(renderer.normalizedMotions[0]?.id).toContain(":happy-bounce");
    expect(renderer.normalizedMotions[0]?.channels).toContain("mouthSmile");
    expect(controller.getActiveMotionPlanId()).toContain(
      ":plan:happy-bounce",
    );
  });

  it("uses globally distinct plan ids across controller sessions", async () => {
    const first = new Live2DPresentationController(new FakeRenderer());
    const second = new Live2DPresentationController(new FakeRenderer());

    await first.playPrimitive("nod");
    await second.playPrimitive("nod");

    expect(first.getActiveMotionPlanId()).not.toBe(
      second.getActiveMotionPlanId(),
    );
  });

  it("uses idle breathing when a model has no authored idle motion", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest({ ...manifest, motionMap: {} });

    const result = await controller.playMotion("idle");

    expect(result.appliedSource).toBe("procedural");
    expect(renderer.normalizedMotions[0]?.id).toContain(":idle-breathe");
    expect(renderer.normalizedMotions[0]?.loopable).toBe(true);
  });

  it("degrades a failing motion driver to validated safe idle", async () => {
    const renderer = new FakeRenderer();
    const failingDriver: MotionDriver = {
      id: "failing-driver",
      version: "1",
      async drive() {
        throw new Error("driver failed");
      },
    };
    const controller = new Live2DPresentationController(
      renderer,
      { motionDriver: failingDriver },
    );
    controller.setManifest({ ...manifest, motionMap: {} });

    const result = await controller.playMotion("nod");

    expect(result.appliedSource).toBe("procedural");
    expect(renderer.normalizedMotions[0]?.id).toContain(":safe-idle");
  });

  it("uses cached semantic intent and records training telemetry", async () => {
    const renderer = new FakeRenderer();
    const decisions: MotionDecisionRecord[] = [];
    const executions: MotionExecutionRecord[] = [];
    const invalid: MotionInvalidRecord[] = [];
    const telemetry: MotionTelemetry = {
      async recordDecision(record) { decisions.push(record); },
      async recordExecution(record) { executions.push(record); },
      async recordInvalid(record) { invalid.push(record); },
    };
    const cache: MotionCache = {
      async get(key) {
        return {
          key,
          plannerVersion: "display-plan-adapter-v1",
          modelProfileVersion: "default-v1",
          primitiveVersion: "1.0.0",
          createdAt: "2026-08-12T00:00:00.000Z",
          intent: {
            id: "old-id",
            source: "rule",
            intent: "deny",
            emotion: "neutral",
            durationMs: 920,
            intensity: 0.5,
            loopable: false,
            interruptible: true,
            priority: "speech",
          },
        };
      },
      async set() {},
      async clear() {},
    };
    const controller = new Live2DPresentationController(renderer, {
      motionCache: cache,
      motionTelemetry: telemetry,
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    await controller.playMotion("nod", "neutral", "不是这个答案。");
    await Promise.resolve();

    expect(decisions[0]).toMatchObject({
      assistantText: "不是这个答案。",
      cacheHit: true,
      selectedIntent: { source: "cache", intent: "deny" },
    });
    expect(executions[0]).toMatchObject({ fallbackUsed: false });
    expect(invalid).toEqual([]);
  });

  it("publishes a replayable summary for a real playback surface", async () => {
    const renderer = new FakeRenderer();
    const playbacks: MotionPlaybackSummary[] = [];
    const controller = new Live2DPresentationController(renderer, {
      playbackSurface: "pet",
      onMotionExecuted: (playback) => playbacks.push(playback),
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    await controller.playMotion("speaking", "neutral", "我来解释一下。 ");

    expect(playbacks).toHaveLength(1);
    expect(playbacks[0]).toMatchObject({
      assistantText: "我来解释一下。 ",
      surface: "pet",
      primitive: "explain-small",
      normalizedClip: { frames: expect.any(Array) },
    });
  });

  it("does not invoke telemetry when local data collection is disabled", async () => {
    const renderer = new FakeRenderer();
    let callCount = 0;
    const telemetry: MotionTelemetry = {
      async recordDecision() { callCount += 1; },
      async recordExecution() { callCount += 1; },
      async recordInvalid() { callCount += 1; },
    };
    let playbackCount = 0;
    const controller = new Live2DPresentationController(renderer, {
      motionTelemetry: telemetry,
      motionDataCollectionEnabled: () => false,
      onMotionExecuted: () => { playbackCount += 1; },
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    await controller.playMotion("idle", "neutral", "保持安静。 ");
    await Promise.resolve();

    expect(callCount).toBe(0);
    expect(playbackCount).toBe(0);
  });

  it("plans semantic motion locally when speaking has no explicit gesture", async () => {
    const renderer = new FakeRenderer();
    const cache: MotionCache = {
      async get() { return null; },
      async set() {},
      async clear() {},
    };
    const controller = new Live2DPresentationController(renderer, {
      motionCache: cache,
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    await controller.playMotion("speaking", "neutral", "不对，这个结论需要修正。");

    expect(renderer.normalizedMotions[0]?.id).toContain(":shake");
    expect(renderer.normalizedMotions[0]?.channels).toContain("headYaw");
  });
});
