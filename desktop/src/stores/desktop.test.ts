import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { planFromText } from "@/domain/displayPlan";
import type { DesktopRuntimeSnapshot } from "@/domain/desktopWindowProtocol";
import { useDesktopStore } from "./desktop";

describe("desktop store pet transitions", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("waits for emerge to finish before entering chat", () => {
    const store = useDesktopStore();

    store.enterPetChat();

    expect(store.petRuntime.status).toBe("emerging");
    expect(store.pendingAfterEmerge.enterChat).toBe(true);

    store.completePetEmerge();

    expect(store.petRuntime.status).toBe("chat");
    expect(store.pendingAfterEmerge).toEqual({
      enterChat: false,
      action: { type: "none" },
    });
  });

  it("waits for emerge to finish before showing an error", () => {
    const store = useDesktopStore();

    store.applyDesktopEvent({
      type: "notification.error",
      source: "local",
      at: "2026-06-12T00:00:00.000Z",
      message: "Connection failed",
    });

    expect(store.petRuntime.status).toBe("emerging");
    expect(store.pendingAfterEmerge.action).toEqual({ type: "error" });

    store.completePetEmerge();

    expect(store.petRuntime.status).toBe("error");
    expect(store.pendingAfterEmerge.action).toEqual({ type: "none" });
  });

  it("cancels a pending presentation when emergence retreats", () => {
    const store = useDesktopStore();
    const displayPlan = planFromText("Delayed reply", "happy");

    store.applyDesktopEvent({
      type: "message.completed",
      source: "local",
      at: "2026-06-12T00:00:00.000Z",
      sessionId: "test-session",
      displayPlan,
    });
    expect(store.pendingAfterEmerge.action).toEqual({
      type: "presentation",
    });

    store.requestPetRetreat();
    store.completePetRetreat();
    store.requestPetEmerge();
    store.completePetEmerge();

    expect(store.pendingAfterEmerge.action).toEqual({ type: "none" });
    expect(store.petRuntime.status).toBe("emerged");
    expect(store.speaking).toBe(false);
  });

  it("ignores a stale retreat completion without losing emerge work", () => {
    const store = useDesktopStore();

    store.enterPetChat();

    expect(store.completePetRetreat()).toBe(false);
    expect(store.petRuntime.status).toBe("emerging");
    expect(store.pendingAfterEmerge.enterChat).toBe(true);
  });

  it("starts a pending reply inside chat after emergence", () => {
    const store = useDesktopStore();
    const displayPlan = planFromText("Chat reply", "happy");

    store.enterPetChat();
    store.applyDesktopEvent({
      type: "message.completed",
      source: "local",
      at: "2026-06-12T00:00:00.000Z",
      sessionId: "test-session",
      displayPlan,
    });
    store.completePetEmerge();
    store.setSegment(0, true);

    expect(store.petRuntime.status).toBe("chat");
    expect(store.petRuntime.speaking).toBe(true);
    expect(store.petRuntime.bubbleText).toBe("Chat reply");
  });

  it("shows timed subtitle segments without enabling lip sync", () => {
    const store = useDesktopStore();
    const displayPlan = planFromText("Subtitle only", "thinking");

    store.requestPetEmerge();
    store.completePetEmerge();
    store.applyDesktopEvent({
      type: "message.completed",
      source: "local",
      at: "2026-06-12T00:00:00.000Z",
      sessionId: "test-session",
      displayPlan,
    });
    store.setSegment(0, false);

    expect(store.petRuntime.status).toBe("emerged");
    expect(store.petRuntime.speaking).toBe(false);
    expect(store.petRuntime.bubbleText).toBe("Subtitle only");
    expect(store.petRuntime.emotion).toBe("thinking");
  });

  it("applies local config snapshots only when their revision changes", () => {
    const store = useDesktopStore();
    const snapshot = {
      connected: true,
      sessionId: "test-session",
      activePlan: null,
      activePresentation: null,
      petRuntime: store.petRuntime,
      localConfig: {
        ...store.localConfig,
        performanceMode: "active",
      },
      localConfigVersion: 1,
      expressionMapVersion: 0,
      motionMapVersion: 0,
    } satisfies DesktopRuntimeSnapshot;

    store.applyRuntimeSnapshot(snapshot);
    store.applyRuntimeSnapshot({
      ...snapshot,
      localConfig: {
        ...snapshot.localConfig,
        performanceMode: "power_saver",
      },
    });

    expect(store.localConfig.performanceMode).toBe("active");
  });
});
