import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  tauri: true,
  permissionGranted: false,
  requestPermission: vi.fn(async () => "granted" as const),
  sendNotification: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => mocks.tauri,
}));
vi.mock("@tauri-apps/plugin-notification", () => ({
  isPermissionGranted: async () => mocks.permissionGranted,
  requestPermission: mocks.requestPermission,
  sendNotification: mocks.sendNotification,
}));

import { showDesktopNotification } from "./desktopNotification";

describe("desktop notifications", () => {
  beforeEach(() => {
    mocks.tauri = true;
    mocks.permissionGranted = false;
    mocks.requestPermission.mockResolvedValue("granted");
    vi.clearAllMocks();
  });

  it("requests permission once and sends the notification", async () => {
    await expect(
      showDesktopNotification({ title: "Nahida", body: "休息一下吧" }),
    ).resolves.toBe(true);

    expect(mocks.requestPermission).toHaveBeenCalledOnce();
    expect(mocks.sendNotification).toHaveBeenCalledWith({
      title: "Nahida",
      body: "休息一下吧",
    });
  });

  it("does nothing in the browser preview", async () => {
    mocks.tauri = false;

    await expect(
      showDesktopNotification({ title: "Nahida", body: "test" }),
    ).resolves.toBe(false);
    expect(mocks.sendNotification).not.toHaveBeenCalled();
  });

  it("contains native permission errors", async () => {
    mocks.requestPermission.mockRejectedValueOnce(new Error("unavailable"));
    vi.spyOn(console, "warn").mockImplementation(() => {});

    await expect(
      showDesktopNotification({ title: "Nahida", body: "test" }),
    ).resolves.toBe(false);
    expect(mocks.sendNotification).not.toHaveBeenCalled();
  });
});
