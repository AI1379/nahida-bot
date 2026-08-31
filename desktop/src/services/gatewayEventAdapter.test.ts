import { describe, expect, it } from "vitest";

import {
  gatewayNodeEventAdapter,
  type GatewayNodeRawEvent,
} from "./gatewayEventAdapter";

describe("GatewayNodeEventAdapter", () => {
  it("maps plugin runtime snapshots", () => {
    const event = gatewayNodeEventAdapter.toDesktopEvent({
      type: "gateway_event",
      at: "2026-08-31T00:00:00Z",
      envelope: {
        version: "1.0",
        kind: "event",
        event: "plugin.runtime.sync",
        payload: {
          generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          revision: 1,
          plugins: [
            {
              id: "nahida.pomodoro",
              name: "Pomodoro",
              version: "0.1.0",
              state: "enabled",
              configured_enabled: true,
              runtimes: {
                desktop: {
                  entrypoint: "builtin:nahida.pomodoro",
                  mode: "builtin",
                },
              },
              contributes: {},
            },
          ],
        },
      },
    });

    expect(event).toMatchObject({
      type: "plugin.runtime.sync",
      snapshot: {
        generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        revision: 1,
      },
    });
  });

  it("preserves the invocation id for renderer acknowledgement", () => {
    const event = gatewayNodeEventAdapter.toDesktopEvent({
      type: "capability_invoke",
      at: "2026-07-09T00:00:00Z",
      invokeId: "inv_test",
      capability: "desktop.notification.show",
      arguments: { message: "hello" },
    });

    expect(event).toMatchObject({
      type: "capability.invoked",
      invocationId: "inv_test",
      capability: "desktop.notification.show",
    });
  });

  it("maps registered status to a gateway connection event", () => {
    const event = gatewayNodeEventAdapter.toDesktopEvent({
      type: "status_changed",
      at: "2026-07-09T00:00:00Z",
      status: {
        connected: true,
        registered: true,
        nodeId: "desktop-local",
        gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
      },
    });

    expect(event).toMatchObject({
      type: "connection.changed",
      source: "gateway",
      connected: true,
      gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
      nodeId: "desktop-local",
    });
  });

  it("marks rejected credentials as authentication required", () => {
    const event = gatewayNodeEventAdapter.toDesktopEvent({
      type: "status_changed",
      at: "2026-07-09T00:00:00Z",
      status: {
        connected: false,
        registered: false,
        nodeId: "desktop-local",
        gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
        lastError: "HTTP error: 401 Unauthorized",
      },
    });

    expect(event).toMatchObject({
      type: "connection.changed",
      connected: false,
      authRequired: true,
    });
  });

  it("maps agent.message.completed display plans from snake_case payloads", () => {
    const rawEvent = {
      type: "gateway_event",
      at: "2026-07-09T00:00:00Z",
      envelope: {
        version: "1.0",
        kind: "event",
        event: "agent.message.completed",
        payload: {
          session_id: "milky:private:10001",
          text: "今天的计划已经整理好了。",
          display_plan: {
            version: "1.0",
            text: "今天的计划已经整理好了。",
            segments: [
              {
                text: "今天的计划已经整理好了。",
                emotion: "happy",
                motion: "nod",
                pause_after_ms: 250,
              },
            ],
          },
        },
      },
    } satisfies GatewayNodeRawEvent;

    const event = gatewayNodeEventAdapter.toDesktopEvent(rawEvent);

    expect(event).toMatchObject({
      type: "message.completed",
      source: "gateway",
      sessionId: "milky:private:10001",
      displayPlan: {
        text: "今天的计划已经整理好了。",
        segments: [
          {
            text: "今天的计划已经整理好了。",
            emotion: "happy",
            motion: "nod",
            pauseAfterMs: 250,
          },
        ],
      },
    });
  });

  it("maps agent failures and scheduled reminders", () => {
    const failed = gatewayNodeEventAdapter.toDesktopEvent({
      type: "gateway_event",
      at: "2026-07-09T00:00:00Z",
      envelope: {
        version: "1.0",
        kind: "event",
        event: "agent.message.error",
        payload: { session_id: "test:private:c1", error: "provider failed" },
      },
    });
    const reminder = gatewayNodeEventAdapter.toDesktopEvent({
      type: "gateway_event",
      at: "2026-07-09T00:01:00Z",
      envelope: {
        version: "1.0",
        kind: "event",
        event: "notification.reminder",
        payload: {
          job_id: "cron-1",
          session_id: "test:private:c1:cron:cron-1",
          message: "该休息一下了。",
        },
      },
    });

    expect(failed).toMatchObject({
      type: "notification.error",
      sessionId: "test:private:c1",
      message: "provider failed",
    });
    expect(reminder).toMatchObject({
      type: "notification.reminder",
      message: "该休息一下了。",
      dedupeKey: "cron-1",
    });
  });
});
