import { describe, expect, it } from "vitest";

import {
  live2dRendererProfiles,
  petTriggerDefaults,
  resolveLive2DTargetFps,
} from "./desktopRuntimeDefaults";

describe("Live2D renderer profiles", () => {
  it("caps the main-window preview below the pet renderer", () => {
    expect(live2dRendererProfiles.preview.maxDevicePixelRatio).toBe(1);
    expect(live2dRendererProfiles.preview.fpsByMode.active).toBe(30);
    expect(live2dRendererProfiles.preview.maxDevicePixelRatio).toBeLessThan(
      live2dRendererProfiles.pet.maxDevicePixelRatio,
    );
    expect(live2dRendererProfiles.preview.fpsByMode.active).toBeLessThan(
      live2dRendererProfiles.pet.fpsByMode.active,
    );
  });

  it("never lets a motion boost wake a suspended renderer", () => {
    expect(resolveLive2DTargetFps("pet", "suspended", true)).toBe(0);
    expect(resolveLive2DTargetFps("preview", "suspended", true)).toBe(0);
  });

  it("limits preview motion boosts to the preview active frame rate", () => {
    expect(resolveLive2DTargetFps("preview", "idle", true)).toBe(30);
    expect(resolveLive2DTargetFps("preview", "speaking", false)).toBe(24);
  });
});

describe("pet trigger defaults", () => {
  it("hides farther away than it wakes so peek cannot flip-flop", () => {
    expect(petTriggerDefaults.hideDistancePx).toBeGreaterThan(
      petTriggerDefaults.wakeDistancePx,
    );
  });

  it("keeps the chat session alive longer than a plain emergence", () => {
    expect(petTriggerDefaults.chatIdleTimeoutMs).toBeGreaterThan(
      petTriggerDefaults.autoRetreatMs,
    );
  });
});
