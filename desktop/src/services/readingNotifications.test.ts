import { beforeEach, describe, expect, it, vi } from "vitest";
import { acceptReadingNotification, parseReadingNotification, readNotificationInbox } from "./readingNotifications";

const mocks = vi.hoisted(() => ({
  values: new Map<string, unknown>(),
  invoke: vi.fn(), save: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke, isTauri: () => true }));
vi.mock("@tauri-apps/plugin-store", () => ({ load: async () => ({
  get: async (key: string) => structuredClone(mocks.values.get(key)),
  set: async (key: string, value: unknown) => { mocks.values.set(key, structuredClone(value)); },
  save: mocks.save,
}) }));

const args = { notification_id: "notification-1", title: "今日简报", body: "任务内容\n".repeat(100) };

describe("silent durable reading notifications", () => {
  beforeEach(() => {
    mocks.values.clear();
    mocks.invoke.mockReset().mockResolvedValue({ locked: false, do_not_disturb: false });
    mocks.save.mockReset().mockResolvedValue(undefined);
  });

  it("persists before acceptance and does not redisplay a duplicate", async () => {
    const display = vi.fn(() => expect(mocks.save).toHaveBeenCalledOnce());
    const result = await acceptReadingNotification(args, "owner-a", display);
    expect(result.status).toBe("accepted");
    expect(await acceptReadingNotification(args, "owner-a", display)).toEqual({ status: "accepted", duplicate: true });
    expect(display).toHaveBeenCalledOnce();
    expect(await readNotificationInbox("owner-a")).toHaveLength(1);
    expect(await readNotificationInbox("owner-b")).toEqual([]);
    // This path has no speech or system-notification dependency.
    expect(mocks.invoke).toHaveBeenCalledWith("desktop_context_read");
  });

  it("does not acknowledge an in-memory record after disk persistence fails", async () => {
    mocks.save.mockRejectedValueOnce(new Error("disk full"));
    const display = vi.fn();
    await expect(acceptReadingNotification(args, "owner-a", display)).rejects.toThrow("disk full");
    expect(await readNotificationInbox("owner-a")).toEqual([]);
    expect(display).not.toHaveBeenCalled();
    expect((await acceptReadingNotification(args, "owner-a", display)).status).toBe("accepted");
    expect(mocks.save).toHaveBeenCalledTimes(2);
  });

  it.each([
    { locked: true, do_not_disturb: false },
    { locked: null, do_not_disturb: false },
    { locked: false, do_not_disturb: true },
  ])("rechecks local lock and quiet state just before delivery", async (state) => {
    mocks.invoke.mockResolvedValue(state);
    const display = vi.fn();
    expect(await acceptReadingNotification(args, "owner-a", display)).toEqual({ status: "suppressed" });
    expect(display).not.toHaveBeenCalled();
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("serializes concurrent retries of the same ID", async () => {
    const display = vi.fn();
    await Promise.all([acceptReadingNotification(args, "owner-a", display), acceptReadingNotification(args, "owner-a", display)]);
    expect(display).toHaveBeenCalledOnce();
  });

  it("bounds content before it enters the reading UI", () => {
    expect(() => parseReadingNotification({ ...args, body: "x".repeat(12001) })).toThrow();
    expect(() => parseReadingNotification({ ...args, body: " " })).toThrow();
  });
});
