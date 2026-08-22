import { isTauri } from "@tauri-apps/api/core";
import { load, type Store } from "@tauri-apps/plugin-store";

import type { TurnRecord, TurnStatus } from "@/domain/conversation";

const STORE_FILE = "conversation-history.json";
const STORE_KEY = "turns-v1";
const BROWSER_STORAGE_KEY = "nahida.desktop.turns.v1";
const MAX_TURNS = 200;
const turnStatuses = new Set<TurnStatus>([
  "submitting",
  "accepted",
  "generating",
  "synthesizing",
  "playing",
  "completed",
  "failed",
]);

let storePromise: Promise<Store> | null = null;
let writeQueue: Promise<void> = Promise.resolve();

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function cleanText(value: unknown, maximum: number): string {
  return typeof value === "string" ? value.slice(0, maximum) : "";
}

function sanitizeTurn(
  value: unknown,
  recoverInterrupted: boolean,
): TurnRecord | null {
  if (!isRecord(value)) return null;
  const id = cleanText(value.id, 128).trim();
  const sessionId = cleanText(value.sessionId, 256).trim();
  const createdAt = cleanText(value.createdAt, 64).trim();
  const rawStatus = value.status;
  if (
    !id ||
    !sessionId ||
    !createdAt ||
    typeof rawStatus !== "string" ||
    !turnStatuses.has(rawStatus as TurnStatus)
  ) {
    return null;
  }
  const storedStatus = rawStatus as TurnStatus;
  const status: TurnStatus =
    !recoverInterrupted ||
    storedStatus === "completed" ||
    storedStatus === "failed"
      ? storedStatus
      : "failed";
  return {
    id,
    sessionId,
    userText: cleanText(value.userText, 20_000),
    assistantText: cleanText(value.assistantText, 100_000),
    status,
    createdAt,
    updatedAt: cleanText(value.updatedAt, 64).trim() || createdAt,
    presentationId: cleanText(value.presentationId, 256).trim() || undefined,
    error:
      cleanText(value.error, 4_000).trim() ||
      (recoverInterrupted && status === "failed" && storedStatus !== "failed"
        ? "This turn was interrupted when the desktop app closed."
        : undefined),
  };
}

function sanitizeTurns(value: unknown, recoverInterrupted = false): TurnRecord[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((turn) => sanitizeTurn(turn, recoverInterrupted))
    .filter((turn): turn is TurnRecord => turn !== null)
    .slice(0, MAX_TURNS);
}

async function conversationStore(): Promise<Store> {
  storePromise ??= load(STORE_FILE, { autoSave: false });
  return storePromise;
}

export async function readConversationHistory(): Promise<TurnRecord[]> {
  if (isTauri()) {
    return sanitizeTurns(
      await (await conversationStore()).get(STORE_KEY),
      true,
    );
  }
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(BROWSER_STORAGE_KEY);
    return sanitizeTurns(raw ? JSON.parse(raw) : [], true);
  } catch {
    return [];
  }
}

export function writeConversationHistory(
  turns: readonly TurnRecord[],
): Promise<void> {
  const snapshot = sanitizeTurns(turns).slice(0, MAX_TURNS);
  writeQueue = writeQueue.catch(() => undefined).then(async () => {
    if (isTauri()) {
      const store = await conversationStore();
      await store.set(STORE_KEY, snapshot);
      await store.save();
      return;
    }
    if (typeof window !== "undefined") {
      window.localStorage.setItem(BROWSER_STORAGE_KEY, JSON.stringify(snapshot));
    }
  });
  return writeQueue;
}
