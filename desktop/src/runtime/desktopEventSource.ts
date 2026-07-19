import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { DesktopEvent } from "@/domain/runtime";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import {
  gatewayNodeEventAdapter,
  mockGatewayEventAdapter,
  type GatewayNodeRawEvent,
  type GatewayNodeStatus,
} from "@/services/gatewayEventAdapter";
import { mockBackend } from "@/services/mockBackend";

export type DesktopEventHandler = (event: DesktopEvent) => void;
const gatewayNodeEventName = "nahida://gateway-node/event";

export interface DesktopEventSourceOptions {
  connection?: GatewayConnectionSettings;
}

export interface DesktopEventSource {
  start(
    handler: DesktopEventHandler,
    options?: DesktopEventSourceOptions,
  ): void;
  stop(): void;
  submitUserMessage(text: string, sessionId?: string): void;
  submitMockLlmResult(rawOutput: string): void;
}

export class MockDesktopEventSource implements DesktopEventSource {
  private unsubscribe: (() => void) | null = null;

  start(handler: DesktopEventHandler): void {
    if (this.unsubscribe) return;
    this.unsubscribe = mockBackend.subscribe((event) => {
      const desktopEvent = mockGatewayEventAdapter.toDesktopEvent(event);
      if (desktopEvent) {
        handler(desktopEvent);
      }
    });
    mockBackend.connect();
  }

  stop(): void {
    mockBackend.disconnect();
    this.unsubscribe?.();
    this.unsubscribe = null;
  }

  submitUserMessage(text: string): void {
    mockBackend.submitUserMessage(text);
  }

  submitMockLlmResult(rawOutput: string): void {
    mockBackend.submitMockLlmResult(rawOutput);
  }
}

interface GatewayNodeConnectPayload {
  url?: string;
  token?: string;
  nodeId?: string;
  displayName?: string;
  defaultSessionId?: string;
}

function buildConnectPayload(
  connection: GatewayConnectionSettings | undefined,
): GatewayNodeConnectPayload | undefined {
  if (!connection) return undefined;
  return {
    url: connection.gatewayWsUrl,
    token: connection.nodeToken,
    nodeId: connection.nodeId,
    displayName: connection.displayName,
    defaultSessionId: connection.defaultSessionId,
  };
}

export class TauriGatewayNodeEventSource implements DesktopEventSource {
  private unlisten: UnlistenFn | null = null;
  private handler: DesktopEventHandler | null = null;
  private status: GatewayNodeStatus | null = null;

  start(
    handler: DesktopEventHandler,
    options?: DesktopEventSourceOptions,
  ): void {
    if (this.handler) return;
    this.handler = handler;
    void this.startGatewayNode(options?.connection);
  }

  stop(): void {
    void invoke("gateway_node_disconnect");
    this.emitConnection(false);
    this.unlisten?.();
    this.unlisten = null;
    this.handler = null;
    this.status = null;
  }

  submitUserMessage(text: string, sessionId?: string): void {
    const targetSessionId =
      this.status?.defaultSessionId || sessionId || this.status?.sessionId;
    if (!targetSessionId) {
      this.emitLocalError("No gateway session id is configured for desktop input.");
      return;
    }

    void invoke("gateway_node_submit_input", {
      input: {
        sessionId: targetSessionId,
        text,
      },
    }).catch((error: unknown) => {
      this.emitLocalError(`Failed to submit message: ${String(error)}`);
    });
  }

  submitMockLlmResult(): void {
    this.emitLocalError("Mock LLM result is only available in mock backend mode.");
  }

  private async startGatewayNode(
    connection?: GatewayConnectionSettings,
  ) {
    try {
      this.unlisten = await listen<GatewayNodeRawEvent>(
        gatewayNodeEventName,
        (event) => this.handleGatewayNodeEvent(event.payload),
      );
      this.status = await invoke<GatewayNodeStatus>("gateway_node_connect", {
        config: buildConnectPayload(connection),
      });
    } catch (error) {
      this.emitLocalError(`Gateway node connection failed: ${String(error)}`);
      this.emitConnection(false, String(error));
    }
  }

  private handleGatewayNodeEvent(rawEvent: GatewayNodeRawEvent) {
    if (rawEvent.type === "status_changed") {
      this.status = rawEvent.status;
    }
    const desktopEvent = gatewayNodeEventAdapter.toDesktopEvent(rawEvent);
    if (desktopEvent) {
      this.handler?.(desktopEvent);
    }
  }

  private emitConnection(connected: boolean, reason?: string) {
    this.handler?.({
      type: "connection.changed",
      source: "gateway",
      at: new Date().toISOString(),
      connected,
      reason,
      gatewayUrl: this.status?.gatewayUrl,
      nodeId: this.status?.nodeId,
    });
  }

  private emitLocalError(message: string) {
    this.handler?.({
      type: "notification.error",
      source: "gateway",
      at: new Date().toISOString(),
      message,
    });
  }
}

/**
 * Event source used when the user picks `mode: "gateway"` outside of the
 * packaged Tauri app (e.g. plain browser dev). It surfaces a clear error
 * instead of silently falling back to the mock backend.
 */
export class UnsupportedGatewayEventSource implements DesktopEventSource {
  private emitted = false;
  private readonly reason: string;

  constructor(reason: string) {
    this.reason = reason;
  }

  start(handler: DesktopEventHandler): void {
    if (this.emitted) return;
    this.emitted = true;
    handler({
      type: "connection.changed",
      source: "gateway",
      at: new Date().toISOString(),
      connected: false,
      reason: this.reason,
    });
  }

  stop(): void {
    this.emitted = false;
  }

  submitUserMessage(): void {
    // No-op: gateway mode unavailable.
  }

  submitMockLlmResult(): void {
    // No-op.
  }
}

export function createDesktopEventSource(
  settings?: GatewayConnectionSettings,
): DesktopEventSource {
  if (settings?.mode === "gateway") {
    if (!isTauri()) {
      return new UnsupportedGatewayEventSource(
        "Gateway mode requires the packaged desktop app. Run via Tauri or switch to mock mode.",
      );
    }
    return new TauriGatewayNodeEventSource();
  }
  return new MockDesktopEventSource();
}

/**
 * @deprecated Kept for the legacy Workbench control panel; new code should
 * pass an explicit `GatewayConnectionSettings` to `createDesktopEventSource`.
 */
export function createDefaultDesktopEventSource(): DesktopEventSource {
  return createDesktopEventSource();
}
