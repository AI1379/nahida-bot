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

export const mockGatewayEventAdapter = new MockGatewayEventAdapter();
