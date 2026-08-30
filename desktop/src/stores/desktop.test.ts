import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { planFromText } from "@/domain/displayPlan";
import type { DesktopRuntimeSnapshot } from "@/domain/desktopWindowProtocol";

const showDesktopNotification = vi.hoisted(() => vi.fn(async () => true));
vi.mock("@/services/desktopNotification", () => ({ showDesktopNotification }));

import { useDesktopStore } from "./desktop";

describe("desktop store pet transitions", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
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
    store.startPresentation(store.takePendingPresentations()[0]!);
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
    store.startPresentation(store.takePendingPresentations()[0]!);
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

  it("applies the pomodoro state carried by runtime snapshots", () => {
    const store = useDesktopStore();
    const running: NonNullable<DesktopRuntimeSnapshot["pomodoro"]> = {
      phase: "working",
      round: 2,
      totalRounds: 4,
      startedAt: "2026-08-23T00:00:00.000Z",
      expiresAt: "2026-08-23T00:20:00.000Z",
      remainingSeconds: 1200,
    };

    store.applyRuntimeSnapshot({
      connected: true,
      sessionId: "test-session",
      activePlan: null,
      activePresentation: null,
      petRuntime: store.petRuntime,
      localConfig: store.localConfig,
      localConfigVersion: store.localConfigVersion,
      expressionMapVersion: store.expressionMapVersion,
      motionMapVersion: store.motionMapVersion,
      pomodoro: running,
    });

    expect(store.pomodoroState).toEqual(running);

    store.applyRuntimeSnapshot({
      connected: true,
      sessionId: "test-session",
      activePlan: null,
      activePresentation: null,
      petRuntime: store.petRuntime,
      localConfig: store.localConfig,
      localConfigVersion: store.localConfigVersion,
      expressionMapVersion: store.expressionMapVersion,
      motionMapVersion: store.motionMapVersion,
    });

    expect(store.pomodoroState).toEqual(running);
  });

  it("persists gateway connection updates and bumps the revision", () => {
    const store = useDesktopStore();
    const initialVersion = store.gatewayConnectionVersion;

    store.updateGatewayConnection({
      mode: "gateway",
      gatewayWsUrl: "wss://gateway.example.com/api/nodes/ws",
      nodeToken: "nt_abc.def",
    });

    expect(store.gatewayConnection.mode).toBe("gateway");
    expect(store.gatewayConnection.gatewayWsUrl).toBe(
      "wss://gateway.example.com/api/nodes/ws",
    );
    expect(store.gatewayConnection.nodeToken).toBe("nt_abc.def");
    expect(store.gatewayConnectionVersion).toBe(initialVersion + 1);
  });

  it("rejects invalid URLs when updating gateway connection settings", () => {
    const store = useDesktopStore();

    store.updateGatewayConnection({
      gatewayWsUrl: "javascript:alert(1)",
    });

    expect(store.gatewayConnection.gatewayWsUrl).toBe(
      "ws://127.0.0.1:6185/api/nodes/ws",
    );
  });

  it("clears the node token without losing other settings", () => {
    const store = useDesktopStore();

    store.updateGatewayConnection({
      nodeToken: "nt_abc.def",
      nodeId: "desktop-test",
    });
    store.clearGatewayNodeToken();

    expect(store.gatewayConnection.nodeToken).toBe("");
    expect(store.gatewayConnection.nodeId).toBe("desktop-test");
    expect(store.gatewayPairing.status).toBe("idle");
  });

  it("records the failure reason when a connection drops", () => {
    const store = useDesktopStore();

    store.applyDesktopEvent({
      type: "connection.changed",
      source: "gateway",
      at: "2026-07-19T00:00:00.000Z",
      connected: false,
      reason: "handshake rejected",
    });

    expect(store.connected).toBe(false);
    expect(store.gatewayConnectionError).toBe("handshake rejected");
    expect(store.petRuntime.emotion).toBe("offline");
  });

  it("keeps a submitted turn visible through generation, voice, and completion", () => {
    const store = useDesktopStore();
    const at = "2026-08-22T00:00:00.000Z";
    const turn = store.beginUserTurn("你好", "session-turn", at);

    store.markTurnAccepted(turn.id);
    store.applyDesktopEvent({
      type: "message.started",
      source: "gateway",
      at,
      sessionId: "session-turn",
    });
    store.applyDesktopEvent({
      type: "message.completed",
      source: "gateway",
      at,
      sessionId: "session-turn",
      displayPlan: {
        version: "1.0",
        text: "你好呀",
        segments: [
          {
            text: "你好呀",
            emotion: "happy",
            motion: "wave",
            voice: { style: "bright" },
          },
        ],
      },
    });

    const presentation = store.takePendingPresentations()[0]!;
    expect(store.turns[0]).toMatchObject({
      id: turn.id,
      userText: "你好",
      assistantText: "你好呀",
      status: "synthesizing",
      presentationId: presentation.id,
    });
    store.completePetEmerge();
    store.startPresentation(presentation);
    store.setSegment(0, true, 2345);
    store.markPresentationTurn(presentation.id, "playing");
    expect(store.petRuntime.segmentDurationMs).toBe(2345);
    store.markPresentationTurn(presentation.id, "completed");
    store.finishPresentation();

    expect(store.turns[0]?.status).toBe("completed");
    expect(store.activePresentation).toBeNull();
    expect(store.activePlan).toBeNull();
  });

  it("preserves a failed submission as a visible turn with its reason", () => {
    const store = useDesktopStore();
    store.sessionId = "session-turn";
    const turn = store.beginUserTurn("发送失败", "session-turn");

    store.markTurnFailed(turn.id, "Gateway rejected the message");

    expect(store.currentSessionTurns[0]).toMatchObject({
      userText: "发送失败",
      status: "failed",
      error: "Gateway rejected the message",
    });
  });

  it("enters an explicit auth-required state for rejected credentials", () => {
    const store = useDesktopStore();

    store.applyDesktopEvent({
      type: "connection.changed",
      source: "gateway",
      at: "2026-07-19T00:00:00.000Z",
      connected: false,
      authRequired: true,
      reason: "node token expired",
    });

    expect(store.gatewayConnectionStatus).toBe("auth-required");
    expect(store.gatewayConnectionError).toBe("node token expired");
  });

  it("reports capability success only after applying it", () => {
    const store = useDesktopStore();

    const result = store.applyDesktopEvent({
      type: "capability.invoked",
      source: "gateway",
      at: "2026-08-08T00:00:00.000Z",
      invocationId: "inv_notification",
      capability: "desktop.notification.show",
      arguments: { message: "Renderer applied this" },
    });

    expect(result).toEqual({ ok: true, result: { applied: true } });
    expect(store.transcript[0]?.text).toBe("Renderer applied this");
  });

  it("queues notification announcements with spoken playback", () => {
    const store = useDesktopStore();

    const result = store.applyCapabilityInvoke(
      "desktop.notification.announce",
      { message: "Stand up and stretch" },
    );

    expect(result).toEqual({ ok: true, result: { applied: true } });
    expect(store.pendingPresentations[0]).toMatchObject({
      bubbleText: "Stand up and stretch",
      interruption: "queue",
      ttsEnabled: true,
      displayPlan: {
        segments: [
          {
            text: "Stand up and stretch",
            voice: { style: "neutral" },
          },
        ],
      },
    });
    expect(store.transcript[0]?.text).toBe("Stand up and stretch");
  });

  it("rejects empty and oversized notification announcements", () => {
    const store = useDesktopStore();

    for (const message of ["   ", "x".repeat(301)]) {
      expect(
        store.applyCapabilityInvoke("desktop.notification.announce", {
          message,
        }),
      ).toMatchObject({
        ok: false,
        error: { code: "invalid_arguments", retryable: false },
      });
    }
    expect(store.pendingPresentations).toHaveLength(0);
    expect(store.transcript).toHaveLength(0);
  });

  it("shows native notifications and keeps a transcript record", () => {
    const store = useDesktopStore();

    store.applyCapabilityInvoke("desktop.notification.show", {
      message: "Visible only",
    });

    expect(store.transcript[0]?.text).toBe("Visible only");
    expect(store.activePresentation).toBeNull();
    expect(showDesktopNotification).toHaveBeenCalledWith({
      title: "Nahida Desktop",
      body: "Visible only",
    });
  });

  it("returns structured errors for invalid and unsupported capabilities", () => {
    const store = useDesktopStore();

    expect(
      store.applyCapabilityInvoke("desktop.live2d.play_motion", {
        motion: "not-a-motion",
      }),
    ).toMatchObject({
      ok: false,
      error: { code: "invalid_arguments", retryable: false },
    });
    expect(store.applyCapabilityInvoke("desktop.unsupported", {})).toMatchObject({
      ok: false,
      error: { code: "capability_not_found", retryable: false },
    });
  });

  it("clamps desktop pet window settings to safe dimensions", () => {
    const store = useDesktopStore();

    store.updateDesktopWindowState({
      width: 9999,
      height: 10,
      exposedPx: 1,
      edge: "left",
      alwaysOnTop: false,
    });

    expect(store.localConfig.windowState).toMatchObject({
      width: 720,
      height: 360,
      exposedPx: 16,
      edge: "left",
      alwaysOnTop: false,
    });
  });

  it("updates the visible pet render mode with performance settings", () => {
    const store = useDesktopStore();
    store.requestPetPeek();

    store.updatePerformanceMode("power_saver");

    expect(store.localConfig.performanceMode).toBe("power_saver");
    expect(store.petRuntime.renderMode).toBe("idle");
  });
});
