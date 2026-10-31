import { invoke, isTauri } from "@tauri-apps/api/core";
import { load } from "@tauri-apps/plugin-store";

export interface ReadingNotification {
  id: string;
  title: string;
  body: string;
}

export function parseReadingNotification(args: Record<string, unknown>): ReadingNotification {
  const { notification_id: id, title, body } = args;
  if (typeof id !== "string" || !id || id.length > 160 ||
      typeof title !== "string" || title.length > 80 ||
      typeof body !== "string" || !body.trim() || body.length > 12000) {
    throw new Error("Invalid reading notification");
  }
  return { id, title, body };
}

async function scopeKey(scope: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(scope));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function readNotificationInbox(scope: string): Promise<ReadingNotification[]> {
  if (!isTauri()) return [];
  const storage = await load("personal-notifications.json", { autoSave: false, defaults: {} });
  return await storage.get<ReadingNotification[]>(await scopeKey(scope)) ?? [];
}

let deliveryChain: Promise<unknown> = Promise.resolve();

/** Accepted means durably saved locally. It makes no claim about user attention. */
export function acceptReadingNotification(
  args: Record<string, unknown>, scope: string, display: (item: ReadingNotification) => void,
): Promise<{ status: "accepted" | "suppressed"; duplicate?: boolean }> {
  const next = deliveryChain.then(async () => {
    const item = parseReadingNotification(args);
    const storage = await load("personal-notifications.json", { autoSave: false, defaults: {} });
    const key = await scopeKey(scope);
    const inbox = await storage.get<ReadingNotification[]>(key) ?? [];
    if (inbox.some((previous) => previous.id === item.id)) {
      return { status: "accepted" as const, duplicate: true };
    }
    const context = await invoke<{ locked: boolean | null; do_not_disturb: boolean }>("desktop_context_read");
    if (context.locked !== false || context.do_not_disturb) return { status: "suppressed" as const };
    await storage.set(key, [...inbox.slice(-199), item]);
    try { await storage.save(); }
    catch (error) {
      await storage.set(key, inbox);
      throw error;
    }
    display(item);
    return { status: "accepted" as const };
  });
  deliveryChain = next.catch(() => undefined);
  return next;
}
