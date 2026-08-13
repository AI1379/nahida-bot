import { isTauri } from "@tauri-apps/api/core";
import { load, type Store } from "@tauri-apps/plugin-store";

import type {
  MotionCache,
  MotionCacheEntry,
} from "@/domain/motionRuntime";

const storeFile = "motion-cache.json";
const storeKey = "entries-v1";
const browserStorageKey = "nahida.desktop.motion-cache.v1";
const maximumEntries = 512;

let storePromise: Promise<Store> | null = null;

interface PersistedMotionCache {
  version: 1;
  entries: Record<string, MotionCacheEntry>;
}

export interface MotionCacheKeyInput {
  assistantText: string;
  runtimeStatus: string;
  emotion?: string;
  recentIntentNames: string[];
  modelId: string;
  modelProfileVersion: string;
  plannerVersion: string;
  primitiveVersion: string;
}

function stableHash(source: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function createMotionCacheKey(input: MotionCacheKeyInput): string {
  const canonical = JSON.stringify({
    text: input.assistantText.replace(/\s+/gu, " ").trim().toLowerCase(),
    runtimeStatus: input.runtimeStatus,
    emotion: input.emotion ?? "",
    recentIntentNames: input.recentIntentNames,
    modelId: input.modelId,
    modelProfileVersion: input.modelProfileVersion,
    plannerVersion: input.plannerVersion,
    primitiveVersion: input.primitiveVersion,
  });
  return `motion-v1:${stableHash(canonical)}`;
}

async function motionStore(): Promise<Store> {
  storePromise ??= load(storeFile, { autoSave: false });
  return storePromise;
}

function emptyCache(): PersistedMotionCache {
  return { version: 1, entries: {} };
}

function readBrowserCache(): PersistedMotionCache {
  if (typeof window === "undefined") return emptyCache();
  try {
    const raw = window.localStorage.getItem(browserStorageKey);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object" || !("entries" in parsed)) {
      return emptyCache();
    }
    return parsed as PersistedMotionCache;
  } catch {
    return emptyCache();
  }
}

async function readCache(): Promise<PersistedMotionCache> {
  if (!isTauri()) return readBrowserCache();
  return (await (await motionStore()).get<PersistedMotionCache>(storeKey)) ??
    emptyCache();
}

async function writeCache(cache: PersistedMotionCache): Promise<void> {
  if (!isTauri()) {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(browserStorageKey, JSON.stringify(cache));
    }
    return;
  }
  const store = await motionStore();
  await store.set(storeKey, cache);
  await store.save();
}

function trimmedEntries(
  entries: Record<string, MotionCacheEntry>,
): Record<string, MotionCacheEntry> {
  return Object.fromEntries(
    Object.entries(entries)
      .sort(([, left], [, right]) =>
        right.createdAt.localeCompare(left.createdAt),
      )
      .slice(0, maximumEntries),
  );
}

export class PersistentMotionCache implements MotionCache {
  private writeQueue: Promise<void> = Promise.resolve();

  async get(key: string): Promise<MotionCacheEntry | null> {
    return (await readCache()).entries[key] ?? null;
  }

  set(entry: MotionCacheEntry): Promise<void> {
    this.writeQueue = this.writeQueue.catch(() => undefined).then(async () => {
      const cache = await readCache();
      cache.entries = trimmedEntries({ ...cache.entries, [entry.key]: entry });
      await writeCache(cache);
    });
    return this.writeQueue;
  }

  clear(): Promise<void> {
    this.writeQueue = this.writeQueue
      .catch(() => undefined)
      .then(() => writeCache(emptyCache()));
    return this.writeQueue;
  }
}
