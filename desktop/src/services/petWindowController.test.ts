import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createDefaultLocalDesktopConfig } from "@/domain/config";
import { transitionPetRuntime } from "@/domain/petRuntimeMachine";
import { createInitialPetRuntimeState } from "@/domain/runtime";
import { defaultModelManifest } from "@/config/live2dModelManifests";

const windowMock = vi.hoisted(() => {
  const state = {
    position: { x: 0, y: 0 },
    concurrentPositions: 0,
    maxConcurrentPositions: 0,
  };

  return {
    state,
    setSize: vi.fn(async () => {}),
    setAlwaysOnTop: vi.fn(async () => {}),
    setIgnoreCursorEvents: vi.fn(async () => {}),
    setPosition: vi.fn(async (position: { x: number; y: number }) => {
      state.concurrentPositions += 1;
      state.maxConcurrentPositions = Math.max(
        state.maxConcurrentPositions,
        state.concurrentPositions,
      );
      await Promise.resolve();
      state.position = { x: position.x, y: position.y };
      state.concurrentPositions -= 1;
    }),
    outerPosition: vi.fn(async () => ({ ...state.position })),
    show: vi.fn(async () => {}),
    setFocus: vi.fn(async () => {}),
  };
});

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async () => {}),
  isTauri: () => true,
}));

vi.mock("@tauri-apps/api/dpi", () => ({
  LogicalSize: class {
    readonly width: number;
    readonly height: number;

    constructor(width: number, height: number) {
      this.width = width;
      this.height = height;
    }

    toPhysical(scaleFactor: number) {
      return {
        width: this.width * scaleFactor,
        height: this.height * scaleFactor,
      };
    }
  },
  PhysicalPosition: class {
    readonly x: number;
    readonly y: number;

    constructor(x: number, y: number) {
      this.x = x;
      this.y = y;
    }
  },
}));

vi.mock("@tauri-apps/api/window", () => ({
  currentMonitor: vi.fn(async () => ({
    scaleFactor: 1,
    workArea: {
      position: { x: 0, y: 0 },
      size: { width: 1920, height: 1080 },
    },
  })),
  getCurrentWindow: () => windowMock,
  primaryMonitor: vi.fn(async () => null),
}));

import { PetWindowController } from "./petWindowController";

const windowState = createDefaultLocalDesktopConfig(
  defaultModelManifest,
).windowState;

describe("PetWindowController", () => {
  beforeEach(() => {
    windowMock.state.position = { x: 0, y: 0 };
    windowMock.state.concurrentPositions = 0;
    windowMock.state.maxConcurrentPositions = 0;
    vi.clearAllMocks();
    vi.stubGlobal(
      "requestAnimationFrame",
      (callback: FrameRequestCallback) => {
        queueMicrotask(() => callback(performance.now()));
        return 1;
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("serializes position writes during a slide", async () => {
    let now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => {
      now += 160;
      return now;
    });
    const controller = new PetWindowController();
    const emerging = transitionPetRuntime(
      createInitialPetRuntimeState(),
      "emerge",
      "balanced",
    );
    const settled = new Promise<void>((resolve) => {
      controller.onSlideSettled = () => resolve();
    });

    controller.schedule(emerging, windowState);
    await settled;

    expect(windowMock.state.maxConcurrentPositions).toBe(1);
    expect(windowMock.state.position).toEqual({ x: 1500, y: 460 });
    controller.dispose();
  });

  it("drops a superseded update before it can settle", async () => {
    const controller = new PetWindowController();
    const hidden = createInitialPetRuntimeState();
    const emerging = transitionPetRuntime(hidden, "emerge", "balanced");
    const settled = vi.fn();
    controller.onSlideSettled = settled;

    controller.schedule(emerging, windowState);
    controller.schedule(hidden, windowState);

    await vi.waitFor(() => {
      expect(windowMock.state.position).toEqual({ x: 1878, y: 460 });
    });
    expect(settled).not.toHaveBeenCalled();
    controller.dispose();
  });
});
