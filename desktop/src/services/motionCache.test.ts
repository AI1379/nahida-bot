import { afterEach, describe, expect, it, vi } from "vitest";

import type { MotionCacheEntry } from "@/domain/motionRuntime";
import { createMotionCacheKey, PersistentMotionCache } from "./motionCache";

function fakeBrowserStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

const entry: MotionCacheEntry = {
  key: "motion-v1:test",
  plannerVersion: "rule-v1",
  modelProfileVersion: "default-v1",
  primitiveVersion: "1.0.0",
  createdAt: "2026-08-12T00:00:00.000Z",
  intent: {
    id: "intent-1",
    source: "rule",
    intent: "agree",
    emotion: "happy",
    durationMs: 1180,
    intensity: 0.45,
    loopable: false,
    interruptible: true,
    priority: "speech",
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PersistentMotionCache", () => {
  it("creates stable keys from canonicalized text and versions", () => {
    const base = {
      assistantText: "  HELLO   world ",
      runtimeStatus: "speaking",
      recentIntentNames: ["greet"],
      modelId: "nahida-1080",
      modelProfileVersion: "default-v1",
      plannerVersion: "rule-v1",
      primitiveVersion: "1.0.0",
    };

    expect(createMotionCacheKey(base)).toBe(
      createMotionCacheKey({ ...base, assistantText: "hello world" }),
    );
    expect(createMotionCacheKey(base)).not.toBe(
      createMotionCacheKey({ ...base, plannerVersion: "rule-v2" }),
    );
  });

  it("persists and clears browser-local cache entries", async () => {
    vi.stubGlobal("window", { localStorage: fakeBrowserStorage() });
    const cache = new PersistentMotionCache();

    await cache.set(entry);
    expect(await cache.get(entry.key)).toEqual(entry);
    await cache.clear();
    expect(await cache.get(entry.key)).toBeNull();
  });
});
