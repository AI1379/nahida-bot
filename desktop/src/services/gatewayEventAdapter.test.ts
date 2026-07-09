import { describe, expect, it } from "vitest";

import {
  gatewayNodeEventAdapter,
  type GatewayNodeRawEvent,
} from "./gatewayEventAdapter";

describe("GatewayNodeEventAdapter", () => {
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
});
