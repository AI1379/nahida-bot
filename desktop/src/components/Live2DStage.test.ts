import { afterEach, describe, expect, it, vi } from "vitest";
import { createRenderer, h, reactive, nextTick, ssrContextKey, type ComponentOptions } from "vue";
import type { Live2DModelManifest } from "@/domain/live2d";
import type { SegmentMotionPlayback } from "@/domain/runtime";
import Live2DStage from "./Live2DStage.vue";

const captured = vi.hoisted(() => ({ clips: [] as string[] }));
vi.mock("@/renderers/live2dRenderer", async () => {
  const { neutralNormalizedPose } = await import("@/domain/normalizedPose");
  return { WebLive2DRenderer: class {
    async loadModel() {}
    updateModelConfig() {}
    async applyExpression() { return false; }
    async playModelMotion() { return false; }
    playNormalizedMotion(clip: { id: string }) { captured.clips.push(clip.id); return true; }
    getNormalizedPose() { return { ...neutralNormalizedPose }; }
    getDebugSnapshot() { return null; }
    setFpsMode() {}
    setLipSync() {}
    clearRuntimeMotion() {}
    dispose() {}
  } };
});

interface HostNode { children: HostNode[]; parent?: HostNode; text?: string; }
const hostRenderer = createRenderer<HostNode, HostNode>({
  createElement: () => ({ children: [] }),
  createText: (text) => ({ children: [], text }),
  createComment: (text) => ({ children: [], text }),
  setText: (node, text) => { node.text = text; },
  setElementText: (node, text) => { node.text = text; },
  patchProp: () => {},
  insert(node, parent) { node.parent = parent; parent.children.push(node); },
  remove(node) { if (node.parent) node.parent.children = node.parent.children.filter((child) => child !== node); },
  parentNode: (node) => node.parent ?? null,
  nextSibling: () => null,
});

// Vitest compiles SFCs for SSR in its Node environment. Run their real setup
// and lifecycle with a minimal host so the playback watchers can be tested.
const clientStage: ComponentOptions = {
  ...(Live2DStage as unknown as ComponentOptions), render: () => h("div", { ref: "live2dHost" }),
};

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); captured.clips.length = 0; });

describe("Live2DStage segment playback", () => {
  it("prepares once, waits for the shared start, and restarts identical text for a new segment", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { location: { search: "" }, localStorage: { getItem: () => null, setItem() {} } });
    vi.stubGlobal("document", { hidden: false, addEventListener() {}, removeEventListener() {} });
    const model: Live2DModelManifest = { id: "test", name: "test", entry: "/test.model3.json", source: "bundled",
      motionMap: {}, emotionMap: {}, lipSync: { enabled: false, parameterIds: [] },
      layout: { scale: 1, offsetX: 0, offsetY: 0, edgeExposedPx: 42 } };
    const playback = reactive<{ value: SegmentMotionPlayback }>({ value: { requestId: "first", phase: "preparing" } });
    const ready: string[] = [];
    const app = hostRenderer.createApp({ setup: () => () => h(clientStage, {
      model, emotion: "neutral", expressionKey: "neutral", motion: "speaking", renderMode: "speaking",
      speaking: true, motionDurationMs: 1200, captionText: "好的。", segmentPlayback: playback.value,
      expressionMapVersion: 0, motionMapVersion: 0, debugEnabled: false, devChrome: false,
      motionDataCollectionEnabled: false, onMotionReady: (requestId: string) => ready.push(requestId),
    }) });
    app.provide(ssrContextKey, { modules: new Set() });
    app.mount({ children: [] });
    try {
      await vi.waitFor(() => expect(ready).toEqual(["first"]));
      expect(captured.clips).toEqual([]);
      playback.value = { requestId: "first", phase: "playing", startedAtMs: Date.now() + 100 };
      await nextTick();
      await vi.advanceTimersByTimeAsync(99);
      expect(captured.clips).toEqual([]);
      await vi.advanceTimersByTimeAsync(1);
      expect(captured.clips).toHaveLength(1);
      playback.value = { requestId: "second", phase: "preparing" };
      await vi.waitFor(() => expect(ready).toEqual(["first", "second"]));
      expect(captured.clips).toHaveLength(1);
      playback.value = { requestId: "second", phase: "playing", startedAtMs: Date.now() };
      await nextTick();
      await vi.advanceTimersByTimeAsync(0);
      expect(captured.clips).toHaveLength(2);
    } finally { app.unmount(); }
  });
});
