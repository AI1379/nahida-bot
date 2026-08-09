import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  CapabilityExecutionResult,
  DesktopEvent,
} from "@/domain/runtime";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import { isGatewayAuthError } from "@/domain/gatewayConnection";
import {
  gatewayNodeEventAdapter,
  mockGatewayEventAdapter,
  type GatewayNodeRawEvent,
  type GatewayNodeStatus,
} from "@/services/gatewayEventAdapter";
import { mockBackend } from "@/services/mockBackend";

export type DesktopEventHandler = (
  event: DesktopEvent,
) => unknown;
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
  private generation = 0;

  start(
    handler: DesktopEventHandler,
    options?: DesktopEventSourceOptions,
  ): void {
    if (this.handler) return;
    this.handler = handler;
    const generation = ++this.generation;
    void this.startGatewayNode(options?.connection, generation);
  }

  stop(): void {
    this.generation += 1;
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
    generation = this.generation,
  ) {
    try {
      const unlisten = await listen<GatewayNodeRawEvent>(
        gatewayNodeEventName,
        (event) => this.handleGatewayNodeEvent(event.payload),
      );
      if (generation !== this.generation || !this.handler) {
        unlisten();
        return;
      }
      this.unlisten = unlisten;
      this.status = await invoke<GatewayNodeStatus>("gateway_node_connect", {
        config: buildConnectPayload(connection),
      });
      if (generation !== this.generation || !this.handler) {
        void invoke("gateway_node_disconnect");
      }
    } catch (error) {
      if (generation !== this.generation || !this.handler) return;
      const reason = String(error);
      this.emitLocalError(`Gateway node connection failed: ${reason}`);
      this.emitConnection(false, reason, isGatewayAuthError(reason));
      if (isGatewayAuthError(reason)) this.stopAfterAuthFailure();
    }
  }

  private handleGatewayNodeEvent(rawEvent: GatewayNodeRawEvent) {
    if (
      rawEvent.type === "status_changed" &&
      !rawEvent.status.registered &&
      isGatewayAuthError(rawEvent.status.lastError)
    ) {
      this.status = rawEvent.status;
      const desktopEvent = gatewayNodeEventAdapter.toDesktopEvent(rawEvent);
      if (desktopEvent) this.handler?.(desktopEvent);
      this.stopAfterAuthFailure();
      return;
    }
    if (rawEvent.type === "status_changed") {
      this.status = rawEvent.status;
    }
    const desktopEvent = gatewayNodeEventAdapter.toDesktopEvent(rawEvent);
    if (desktopEvent) {
      if (desktopEvent.type === "capability.invoked") {
        this.executeCapability(desktopEvent);
      } else {
        this.handler?.(desktopEvent);
      }
    }
  }

  private executeCapability(event: Extract<DesktopEvent, { type: "capability.invoked" }>) {
    let execution: CapabilityExecutionResult;
    try {
      const reported = this.handler?.(event);
      execution = isCapabilityExecutionResult(reported)
        ? reported
        : {
            ok: false,
            error: {
              code: "renderer_unavailable",
              message: "main renderer has no capability handler",
              retryable: true,
            },
          };
    } catch (error) {
      execution = {
        ok: false,
        error: {
          code: "capability_failed",
          message: `renderer capability execution failed: ${String(error)}`,
          retryable: false,
        },
      };
    }

    void invoke("gateway_node_complete_capability", {
      result: {
        invokeId: event.invocationId,
        ...execution,
      },
    }).catch((error: unknown) => {
      this.emitLocalError(`Failed to report capability result: ${String(error)}`);
    });
  }

  private emitConnection(
    connected: boolean,
    reason?: string,
    authRequired = false,
  ) {
    this.handler?.({
      type: "connection.changed",
      source: "gateway",
      at: new Date().toISOString(),
      connected,
      reason,
      authRequired,
      gatewayUrl: this.status?.gatewayUrl,
      nodeId: this.status?.nodeId,
    });
  }

  private stopAfterAuthFailure() {
    this.generation += 1;
    this.unlisten?.();
    this.unlisten = null;
    this.handler = null;
    void invoke("gateway_node_disconnect");
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

function isCapabilityExecutionResult(
  value: unknown,
): value is CapabilityExecutionResult {
  if (!value || typeof value !== "object" || !("ok" in value)) return false;
  if (value.ok === true) {
    return "result" in value && value.result !== null && typeof value.result === "object";
  }
  if (value.ok !== false || !("error" in value) || !value.error || typeof value.error !== "object") {
    return false;
  }
  const error = value.error as Record<string, unknown>;
  return (
    typeof error.code === "string" &&
    typeof error.message === "string" &&
    typeof error.retryable === "boolean"
  );
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
