import {
  normalizeDisplayPlan,
  planFromText,
} from "@/domain/displayPlan";
import type { DesktopEvent } from "@/domain/runtime";
import type { MockGatewayEvent } from "@/services/mockBackend";

export interface GatewayEventAdapter<RawEvent = unknown> {
  toDesktopEvent(rawEvent: RawEvent): DesktopEvent | null;
}

export class MockGatewayEventAdapter
  implements GatewayEventAdapter<MockGatewayEvent>
{
  // NOTE: sessionId is not forwarded for connection events because the mock
  // gateway protocol does not carry one. When the real gateway adapter is
  // built, sessionId should be populated on all event types that define it.
  toDesktopEvent(rawEvent: MockGatewayEvent): DesktopEvent | null {
    switch (rawEvent.type) {
      case "gateway.connected":
        return {
          type: "connection.changed",
          source: "mock",
          at: rawEvent.at,
          connected: true,
        };
      case "gateway.disconnected":
        return {
          type: "connection.changed",
          source: "mock",
          at: rawEvent.at,
          connected: false,
        };
      case "agent.message.started":
        return {
          type: "message.started",
          source: "mock",
          at: rawEvent.at,
          sessionId: rawEvent.sessionId,
        };
      case "agent.message.completed":
        return {
          type: "message.completed",
          source: "mock",
          at: rawEvent.at,
          sessionId: rawEvent.sessionId,
          displayPlan: rawEvent.displayPlan,
        };
      case "plugin.error":
        return {
          type: "notification.error",
          source: "mock",
          at: rawEvent.at,
          message: rawEvent.message,
        };
    }
  }
}

export interface GatewayNodeStatus {
  connected: boolean;
  registered: boolean;
  nodeId: string;
  gatewayUrl: string;
  sessionId?: string | null;
  defaultSessionId?: string | null;
  lastError?: string | null;
}

export interface GatewayNodeEnvelope {
  version: string;
  kind: "request" | "response" | "event" | "heartbeat";
  id?: string;
  method?: string;
  event?: string;
  ok?: boolean;
  payload?: Record<string, unknown>;
  error?: {
    code: string;
    message: string;
    retryable?: boolean;
    details?: Record<string, unknown>;
  };
  meta?: Record<string, unknown>;
}

export type GatewayNodeRawEvent =
  | {
      type: "status_changed";
      at: string;
      status: GatewayNodeStatus;
    }
  | {
      type: "gateway_event";
      at: string;
      envelope: GatewayNodeEnvelope;
    }
  | {
      type: "capability_invoke";
      at: string;
      invokeId: string;
      capability: string;
      arguments: Record<string, unknown>;
    };

export class GatewayNodeEventAdapter
  implements GatewayEventAdapter<GatewayNodeRawEvent>
{
  toDesktopEvent(rawEvent: GatewayNodeRawEvent): DesktopEvent | null {
    switch (rawEvent.type) {
      case "status_changed":
        if (rawEvent.status.connected && !rawEvent.status.registered) {
          return null;
        }
        return {
          type: "connection.changed",
          source: "gateway",
          at: rawEvent.at,
          connected: rawEvent.status.registered,
          reason: rawEvent.status.lastError ?? undefined,
          gatewayUrl: rawEvent.status.gatewayUrl,
          nodeId: rawEvent.status.nodeId,
        };
      case "gateway_event":
        return gatewayEnvelopeToDesktopEvent(rawEvent.at, rawEvent.envelope);
      case "capability_invoke":
        return {
          type: "capability.invoked",
          source: "gateway",
          at: rawEvent.at,
          capability: rawEvent.capability,
          arguments: rawEvent.arguments,
        };
    }
  }
}

function gatewayEnvelopeToDesktopEvent(
  at: string,
  envelope: GatewayNodeEnvelope,
): DesktopEvent | null {
  if (envelope.kind !== "event") return null;
  const payload = envelope.payload ?? {};

  switch (envelope.event) {
    case "agent.message.started": {
      const sessionId = readString(payload.session_id ?? payload.sessionId);
      if (!sessionId) return null;
      return {
        type: "message.started",
        source: "gateway",
        at,
        sessionId,
      };
    }
    case "agent.message.completed": {
      const sessionId = readString(payload.session_id ?? payload.sessionId);
      if (!sessionId) return null;
      const text = readString(payload.text) ?? "";
      const displayPlan =
        normalizeDisplayPlan(
          payload.display_plan ?? payload.displayPlan,
          text,
        ) ?? planFromText(text || "Gateway message completed.", "neutral");
      return {
        type: "message.completed",
        source: "gateway",
        at,
        sessionId,
        displayPlan,
      };
    }
    case "plugin.error":
      return {
        type: "notification.error",
        source: "gateway",
        at,
        message:
          readString(payload.message) ??
          readString(payload.error) ??
          "Gateway plugin error.",
      };
    case "gateway.shutdown":
      return {
        type: "notification.error",
        source: "gateway",
        at,
        message: `Gateway is shutting down: ${
          readString(payload.reason) ?? "unknown reason"
        }`,
      };
    case "node.duplicate_connection":
      return {
        type: "notification.error",
        source: "gateway",
        at,
        message: "Another desktop node connection replaced this session.",
      };
    default:
      return null;
  }
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export const mockGatewayEventAdapter = new MockGatewayEventAdapter();
export const gatewayNodeEventAdapter = new GatewayNodeEventAdapter();
