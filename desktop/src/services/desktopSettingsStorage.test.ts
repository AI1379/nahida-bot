import { describe, expect, it } from "vitest";

import { availableModelManifests } from "@/config/live2dModelManifests";
import { createDefaultLocalDesktopConfig } from "@/domain/config";
import { defaultGatewayConnectionSettings } from "@/domain/gatewayConnection";
import {
  createPersistedDesktopSettings,
  sanitizeLocalDesktopConfig,
} from "./desktopSettingsStorage";

function defaultLocalConfig() {
  const selected = availableModelManifests[0];
  if (!selected) throw new Error("A Live2D model manifest is required");
  return createDefaultLocalDesktopConfig(selected, availableModelManifests);
}

describe("desktop settings persistence", () => {
  it("never includes credentials in the ordinary settings snapshot", () => {
    const snapshot = createPersistedDesktopSettings(defaultLocalConfig(), {
      ...defaultGatewayConnectionSettings,
      mode: "gateway",
      nodeToken: "nt_secret.value",
      adminBearerToken: "admin-secret",
    });

    expect(snapshot.gatewayConnection.nodeToken).toBe("");
    expect(snapshot.gatewayConnection.adminBearerToken).toBe("");
    expect(JSON.stringify(snapshot)).not.toContain("nt_secret.value");
    expect(JSON.stringify(snapshot)).not.toContain("admin-secret");
  });

  it("restores all desktop config categories and rejects unsafe values", () => {
    const fallback = defaultLocalConfig();
    const selectedModelId = fallback.selectedModelId;
    const restored = sanitizeLocalDesktopConfig(
      {
        ...fallback,
        performanceMode: "active",
        motionDataCollectionEnabled: false,
        windowState: {
          ...fallback.windowState,
          width: 99999,
          x: 320,
        },
        modelConfigs: {
          [selectedModelId]: {
            ...fallback.modelConfigs[selectedModelId],
            scale: 1.75,
            offsetX: 28,
            performanceProfile: {
              ...fallback.modelConfigs[selectedModelId]?.performanceProfile,
              intensityScale: 1.35,
            },
          },
        },
        ttsSettings: {
          ...fallback.ttsSettings,
          rate: 1.25,
        },
        pomodoro: {
          ...fallback.pomodoro,
          enabled: true,
          workDurationMinutes: 45,
        },
        petTriggers: {
          ...fallback.petTriggers,
          wakeDistancePx: 140,
          autoRetreatMs: 30000,
        },
      },
      fallback,
    );

    expect(restored.performanceMode).toBe("active");
    expect(restored.motionDataCollectionEnabled).toBe(false);
    expect(restored.windowState.width).toBe(2400);
    expect(restored.windowState.x).toBe(320);
    expect(restored.modelConfigs[selectedModelId]?.scale).toBe(1.75);
    expect(restored.modelConfigs[selectedModelId]?.offsetX).toBe(28);
    expect(
      restored.modelConfigs[selectedModelId]?.performanceProfile.intensityScale,
    ).toBe(1.35);
    expect(restored.ttsSettings.rate).toBe(1.25);
    expect(restored.pomodoro.enabled).toBe(true);
    expect(restored.pomodoro.workDurationMinutes).toBe(45);
    expect(restored.petTriggers.wakeDistancePx).toBe(140);
    expect(restored.petTriggers.autoRetreatMs).toBe(30000);
  });

  it("keeps fallback coordinates and nested settings for partial snapshots", () => {
    const fallback = defaultLocalConfig();
    fallback.windowState.x = null;
    fallback.ttsSettings.rate = 1.15;

    const restored = sanitizeLocalDesktopConfig({}, fallback);

    expect(restored.windowState.x).toBeNull();
    expect(restored.ttsSettings.rate).toBe(1.15);
    expect(restored.petTriggers).toEqual(fallback.petTriggers);
  });

  it("clamps unsafe pet trigger values and keeps the hysteresis gap", () => {
    const fallback = defaultLocalConfig();
    const restored = sanitizeLocalDesktopConfig(
      {
        petTriggers: {
          wakeDistancePx: 9999,
          hideDistancePx: 10,
          autoRetreatMs: 50,
          chatIdleTimeoutMs: Number.NaN,
        },
      },
      fallback,
    );

    expect(restored.petTriggers.wakeDistancePx).toBe(240);
    expect(restored.petTriggers.hideDistancePx).toBeGreaterThan(
      restored.petTriggers.wakeDistancePx,
    );
    expect(restored.petTriggers.autoRetreatMs).toBe(1000);
    expect(restored.petTriggers.chatIdleTimeoutMs).toBe(
      fallback.petTriggers.chatIdleTimeoutMs,
    );
  });
});
