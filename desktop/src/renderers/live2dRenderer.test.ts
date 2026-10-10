import { afterEach, describe, expect, it, vi } from "vitest";
import { Live2DModel } from "pixi-live2d-display/cubism4";
import { createNormalizedPoseFrame, type NormalizedMotionClip } from "@/domain/normalizedPose";
import type { Live2DModelManifest } from "@/domain/live2d";
import { WebLive2DRenderer } from "./live2dRenderer";

vi.mock("pixi.js", () => ({
  Ticker: {},
  Application: class {
    view = {};
    ticker = { started: true, maxFPS: 60, start() {}, stop() {} };
    stage = { addChild() {} };
    destroy() {}
  },
}));
vi.mock("pixi-live2d-display/cubism4", () => ({
  Live2DModel: { from: vi.fn(), registerTicker: vi.fn() },
}));

const manifest: Live2DModelManifest = {
  id: "test", name: "test", entry: "/test.model3.json", source: "bundled",
  motionMap: {}, emotionMap: {}, lipSync: { enabled: false, parameterIds: [] },
  layout: { scale: 1, offsetX: 0, offsetY: 0, edgeExposedPx: 42 },
};

async function setup() {
  let now = 0;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("window", { Live2DCubismCore: {}, devicePixelRatio: 1 });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const values = [0, 0];
  const coreModel = {
    _parameterIds: ["ParamAngleX", "ParamAngleY"],
    getParameterValueByIndex: (index: number) => values[index],
    getParameterDefaultValue: () => 0,
    getParameterMinimumValue: () => -30,
    getParameterMaximumValue: () => 30,
    setParameterValueByIndex: (index: number, value: number) => { values[index] = value; },
  };
  const hooks = new Map<string, Set<() => void>>();
  const internalModel = {
    coreModel,
    motionManager: { loadMotion: vi.fn(async () => undefined) },
    on(event: string, callback: () => void) {
      if (!hooks.has(event)) hooks.set(event, new Set());
      hooks.get(event)!.add(callback);
    },
    off(event: string, callback: () => void) { hooks.get(event)?.delete(callback); },
  };
  const model = { internalModel, anchor: { set() {} }, destroy: vi.fn(), motion: vi.fn(async () => false) };
  vi.mocked(Live2DModel.from).mockResolvedValue(model as never);
  const host = { appendChild() {}, clientWidth: 0, clientHeight: 0 } as unknown as HTMLElement;
  const renderer = new WebLive2DRenderer(host);
  await renderer.loadModel(manifest);
  return {
    renderer, values, hooks, model,
    tick(atMs: number, sdkValue?: number) {
      now = atMs;
      hooks.get("afterMotionUpdate")?.forEach((callback) => callback());
      if (sdkValue !== undefined) values[0] = sdkValue;
      hooks.get("beforeModelUpdate")?.forEach((callback) => callback());
    },
  };
}

function clip(channel: "headYaw" | "headPitch"): NormalizedMotionClip {
  return { id: channel, intentId: channel, durationMs: 1000, loopable: false, restoreAtEnd: true,
    channels: [channel], frames: [createNormalizedPoseFrame(0),
      createNormalizedPoseFrame(500, { [channel]: 0.5 }), createNormalizedPoseFrame(1000)] };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("WebLive2DRenderer", () => {
  it("enforces procedural channels after SDK natural movement and physics", async () => {
    const { renderer, values, tick, hooks } = await setup();
    renderer.playNormalizedMotion(clip("headYaw"));
    tick(500, 3);
    expect(values[0]).toBe(15);
    renderer.dispose();
    expect([...hooks.values()].every((callbacks) => callbacks.size === 0)).toBe(true);
  });

  it("returns omitted channels smoothly to their root pose across a motion switch", async () => {
    const { renderer, values, tick } = await setup();
    renderer.playNormalizedMotion(clip("headYaw"));
    tick(500);
    expect(values[0]).toBe(15);
    renderer.playNormalizedMotion(clip("headPitch"));
    tick(590);
    expect(values[0]).toBeCloseTo(7.5);
    // Clearing again must not restart an already pending release.
    renderer.clearRuntimeMotion();
    tick(680);
    expect(values[0]).toBe(0);
    renderer.dispose();
  });

  it("reports a failed authored motion so procedural fallback can run", async () => {
    const { renderer } = await setup();
    expect(await renderer.playModelMotion("Missing", 0)).toBe(false);
    expect(await renderer.prepareModelMotion("Missing", 0)).toBe(false);
    renderer.dispose();
  });
});
