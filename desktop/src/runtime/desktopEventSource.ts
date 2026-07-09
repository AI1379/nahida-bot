import type { DesktopEvent } from "@/domain/runtime";
import { mockGatewayEventAdapter } from "@/services/gatewayEventAdapter";
import { mockBackend } from "@/services/mockBackend";

export type DesktopEventHandler = (event: DesktopEvent) => void;

export interface DesktopEventSource {
  start(handler: DesktopEventHandler): void;
  stop(): void;
  submitUserMessage(text: string): void;
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
