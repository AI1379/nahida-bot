import { describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { gatewayNodeEventAdapter } from "./gatewayEventAdapter";
import { presentationPlanFromDesktopEvent } from "./presentationPlanner";
import { useDesktopStore } from "@/stores/desktop";
import { createPrimitiveMotionPlan } from "./primitiveMotionSynthesizer";

import type { Live2DModelManifest } from "@/domain/live2d";
import type { MotionDriver } from "@/domain/motionDriver";
import type {
  MotionCache,
  MotionMixer,
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
  it("performs a plain gateway reply without requiring voice metadata", async () => {
    setActivePinia(createPinia());
    const store = useDesktopStore();
    store.requestPetEmerge();
    store.completePetEmerge();
    const event = gatewayNodeEventAdapter.toDesktopEvent({
      type: "gateway_event", at: "2026-10-10T00:00:00Z",
      envelope: { version: "1.0", kind: "event", event: "agent.message.completed",
        payload: { session_id: "desktop:private:test", text: "不对，这个结论需要修正。" } },
    })!;
    const presentation = presentationPlanFromDesktopEvent(event)!;
    expect(presentation.ttsEnabled).toBe(false);
    store.startPresentation(presentation);
    store.setSegment(0, false, 2000);
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer);
    await controller.applyPresentation({
      motion: store.currentMotion, emotion: store.currentEmotion,
      expressionKey: store.currentExpressionKey, renderMode: store.petRuntime.renderMode,
      assistantText: store.petRuntime.bubbleText, motionDurationMs: 2000,
    });
    expect(renderer.normalizedMotions.at(-1)?.channels).toContain("headYaw");
    expect(controller.getActiveMotionPlanId()).toContain("shake");
    controller.dispose();
  });

  it("executes all synthesized segments and preserves their timing and parameters", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer, {
      motionSynthesizer: {
        id: "two-step", version: "1",
        async synthesize(intent) {
          return { schemaVersion: 1, id: "two-step-plan", intent, createdAt: "now", durationMs: 1200,
            segments: [
              { type: "primitive", name: "nod", atMs: 0, durationMs: 400, params: { intensity: 0.1 } },
              { type: "primitive", name: "shake", atMs: 500, durationMs: 700, params: { intensity: 0.7 } },
            ], validationWarnings: [] };
        },
      },
    });
    await controller.playMotion("speaking", "neutral", "好的。", 1200);
    const clip = renderer.normalizedMotions.at(-1)!;
    expect(clip.channels).toEqual(expect.arrayContaining(["headYaw", "headPitch"]));
    expect(clip.frames.some((frame) => frame.atMs === 500)).toBe(true);
    expect(clip.frames.find((frame) => frame.atMs === 80)?.headPitch).toBeCloseTo(0.4 * 0.1 / 0.45);
    expect(controller.getActiveMotionPlanId()).toBe("two-step-plan");
    controller.dispose();
  });

  it("reuses semantics from cache with the current speech duration", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer, {
      motionCache: {
        async get(key) {
          return { key, plannerVersion: "1", modelProfileVersion: "1", primitiveVersion: "1", createdAt: "now",
            intent: { id: "old", source: "rule", intent: "agree", emotion: "neutral", durationMs: 900,
              intensity: 0.42, loopable: false, interruptible: true, priority: "speech" } };
        },
        async set() {}, async clear() {},
      },
    });
    await controller.playMotion("speaking", "neutral", "好的。", 4800);
    const clip = renderer.normalizedMotions.at(-1)!;
    expect(clip.durationMs).toBe(4800);
    expect(clip.frames[1]?.atMs).toBe(236);
    controller.dispose();
  });

  it("does not wait for disk writes before preparing playback", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer, {
      motionCache: { async get() { return null; }, set: () => new Promise(() => {}), async clear() {} },
    });
    await controller.playMotion("speaking", "neutral", "好的。");
    expect(renderer.normalizedMotions).toHaveLength(1);
    controller.dispose();
  });

  it("prepares without playback or execution telemetry, then starts at the shared time", async () => {
    const renderer = new FakeRenderer();
    const executions: MotionExecutionRecord[] = [];
    const controller = new Live2DPresentationController(renderer, {
      motionTelemetry: { async recordDecision() {}, async recordExecution(record) { executions.push(record); }, async recordInvalid() {} },
    });
    await controller.applyPresentation({ expressionKey: "neutral", emotion: "neutral", motion: "speaking",
      renderMode: "speaking", assistantText: "好的。", motionDurationMs: 1200 }, true);
    expect(renderer.normalizedMotions).toEqual([]);
    expect(executions).toEqual([]);
    expect(await controller.startPreparedMotion(Date.now() - 200)).toBe(true);
    expect(renderer.normalizedMotions.at(-1)?.durationMs).toBeLessThanOrEqual(1000);
    expect(executions).toHaveLength(1);
    expect(await controller.startPreparedMotion(Date.now())).toBe(false);
    controller.dispose();
  });

  it("drops prepared playback after interruption", async () => {
    vi.useFakeTimers();
    try {
      const renderer = new FakeRenderer();
      const controller = new Live2DPresentationController(renderer);
      await controller.applyPresentation({ expressionKey: "neutral", emotion: "neutral", motion: "speaking",
        renderMode: "speaking", assistantText: "好的。" }, true);
      await controller.playMotion("idle");
      const count = renderer.normalizedMotions.length;
      expect(await controller.startPreparedMotion(Date.now())).toBe(false);
      expect(renderer.normalizedMotions).toHaveLength(count);
      controller.dispose();
    } finally { vi.useRealTimers(); }
  });
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

  it("does not let an older expression load start a stale presentation", async () => {
    const renderer = new FakeRenderer();
    let release: (() => void) | undefined;
    renderer.applyExpression = async () => {
      await new Promise<void>((resolve) => { release = resolve; });
      return true;
    };
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest(manifest);
    const old = controller.applyPresentation({ expressionKey: "neutral", emotion: "neutral",
      motion: "speaking", renderMode: "speaking", assistantText: "旧回复" });
    await controller.playMotion("idle");
    const count = renderer.normalizedMotions.length;
    release!();
    expect((await old).motion.appliedSource).toBe("none");
    expect(renderer.normalizedMotions).toHaveLength(count);
    controller.dispose();
  });

  it("prepares a procedural fallback before audio when an authored motion is missing", async () => {
    const renderer: FakeRenderer & Pick<Live2DRenderer, "prepareModelMotion"> = new FakeRenderer();
    renderer.prepareModelMotion = async () => false;
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest(manifest);
    const result = await controller.applyPresentation({ expressionKey: "neutral", emotion: "neutral", motion: "nod",
      renderMode: "speaking", assistantText: "好的。", motionDurationMs: 1200 }, true);
    expect(result.motion.appliedSource).toBe("procedural");
    expect(renderer.modelMotions).toEqual([]);
    expect(renderer.normalizedMotions).toEqual([]);
    expect(await controller.startPreparedMotion(Date.now())).toBe(true);
    expect(renderer.normalizedMotions.at(-1)?.channels).toContain("headPitch");
    controller.dispose();
  });

  it("preserves pose-keyframe plans even when they contain no primitive", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer, {
      motionSynthesizer: { id: "poses", version: "1", async synthesize(intent) {
        return { schemaVersion: 1, id: "pose-plan", intent, createdAt: "now", durationMs: 1200,
          segments: [{ type: "pose-keyframes", atMs: 300, durationMs: 900, keyframes: [
            { ...neutralNormalizedPose, atMs: 400, headYaw: 0.1 },
            { ...neutralNormalizedPose, atMs: 900 },
          ] }], validationWarnings: [] };
      } },
    });
    await controller.playMotion("speaking", "neutral", "说明一下。", 1200);
    expect(controller.getActiveMotionPlanId()).toBe("pose-plan");
    const clip = renderer.normalizedMotions.at(-1)!;
    expect(clip.frames.find((frame) => frame.atMs === 300)?.headYaw).toBe(0);
    expect(clip.frames.find((frame) => frame.atMs === 700)?.headYaw).toBeCloseTo(0.1);
    controller.dispose();
  });

  it("schedules plan expressions on the shared start and cancels them on interruption", async () => {
    vi.useFakeTimers();
    const renderer = new FakeRenderer();
    renderer.successfulExpression = "wink";
    const controller = new Live2DPresentationController(renderer, {
      motionSynthesizer: { id: "expressions", version: "1", async synthesize(intent, context) {
        if (intent.intent === "idle") return createPrimitiveMotionPlan(intent, context);
        return { schemaVersion: 1, id: "expression-plan", intent, createdAt: "now", durationMs: 1200,
          segments: [
            { type: "primitive", name: "nod", atMs: 0, durationMs: 1200, params: {} },
            { type: "expression", atMs: 300, expressionKey: "wink" },
            { type: "expression", atMs: 700, expressionKey: "wave" },
          ], validationWarnings: [] };
      } },
    });
    try {
      await controller.applyPresentation({ expressionKey: "neutral", emotion: "neutral", motion: "speaking",
        renderMode: "speaking", assistantText: "说明一下。", motionDurationMs: 1200 }, true);
      await controller.startPreparedMotion(Date.now() - 100);
      await vi.advanceTimersByTimeAsync(199);
      expect(renderer.expressions).toEqual([]);
      await vi.advanceTimersByTimeAsync(1);
      expect(renderer.expressions).toEqual(["wink"]);
      await controller.playMotion("idle");
      await vi.advanceTimersByTimeAsync(1000);
      expect(renderer.expressions).toEqual(["wink"]);
    } finally { controller.dispose(); vi.useRealTimers(); }
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

    await controller.playMotion("speaking", "neutral", "我来解释一下。 ", 4321);

    expect(playbacks).toHaveLength(1);
    expect(playbacks[0]).toMatchObject({
      assistantText: "我来解释一下。 ",
      surface: "pet",
      primitive: "explain-small",
      normalizedClip: { frames: expect.any(Array) },
      intent: { durationMs: 4321 },
    });
  });

  it("discards an older async motion result after a newer request starts", async () => {
    const renderer = new FakeRenderer();
    const gate: { release?: () => void } = {};
    let cacheReads = 0;
    const cache: MotionCache = {
      async get() {
        cacheReads += 1;
        if (cacheReads === 1) {
          await new Promise<void>((resolve) => {
            gate.release = resolve;
          });
        }
        return null;
      },
      async set() {},
      async clear() {},
    };
    const controller = new Live2DPresentationController(renderer, {
      motionCache: cache,
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    const stale = controller.playMotion("speaking", "neutral", "旧回复");
    await Promise.resolve();
    await controller.playMotion("idle");
    const appliedAfterNewRequest = renderer.normalizedMotions.length;
    gate.release?.();
    await stale;

    expect(renderer.normalizedMotions).toHaveLength(appliedAfterNewRequest);
    controller.dispose();
  });

  it("mixes persistent idle and speech layers instead of replacing the mixer input", async () => {
    const renderer = new FakeRenderer();
    const sources: string[][] = [];
    const mixer: MotionMixer = {
      id: "capturing-mixer",
      version: "1",
      mix(layers) {
        sources.push(layers.map((layer) => layer.source));
        return layers.at(-1)?.clip ?? null;
      },
    };
    const controller = new Live2DPresentationController(renderer, {
      motionMixer: mixer,
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    await controller.playMotion("idle");
    await controller.playMotion("speaking");

    expect(sources.at(-1)).toEqual(expect.arrayContaining(["idle", "speech"]));
    controller.dispose();
  });

  it("does not collect idle or window-transition motions as rateable data", async () => {
    const renderer = new FakeRenderer();
    let callCount = 0;
    const telemetry: MotionTelemetry = {
      async recordDecision() { callCount += 1; },
      async recordExecution() { callCount += 1; },
      async recordInvalid() { callCount += 1; },
    };
    const controller = new Live2DPresentationController(renderer, {
      motionTelemetry: telemetry,
    });
    controller.setManifest({ ...manifest, motionMap: {} });

    await controller.playMotion("idle", "neutral", "上一条气泡文本");
    await controller.playMotion("emerge", "happy", "上一条气泡文本");

    expect(callCount).toBe(0);
    controller.dispose();
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
