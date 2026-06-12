import {
  normalizeDisplayPlan,
  planFromLlmOutput,
  planFromText,
} from "@/domain/displayPlan";
import type { DisplayPlan } from "@/domain/displayPlan";
import { mockDesktopDefaults } from "@/config/mockDefaults";

export type MockGatewayEvent =
  | {
      type: "gateway.connected" | "gateway.disconnected";
      at: string;
    }
  | {
      type: "agent.message.started";
      at: string;
      sessionId: string;
    }
  | {
      type: "agent.message.completed";
      at: string;
      sessionId: string;
      displayPlan: DisplayPlan;
    }
  | {
      type: "plugin.error";
      at: string;
      message: string;
    };

export type MockGatewayEventHandler = (event: MockGatewayEvent) => void;

const demoPlan = normalizeDisplayPlan({
  version: "1.0",
  text: "我已经连接到 mock backend。现在可以先验证桌宠表情、文本分段和表现计划解析。",
  segments: [
    {
      text: "我已经连接到 mock backend。",
      emotion: "happy",
      motion: "nod",
      pauseAfterMs: 250,
      voice: { style: "bright", speed: 1 },
    },
    {
      text: "现在可以先验证桌宠表情、文本分段和表现计划解析。",
      emotion: "thinking",
      expression: "star",
      motion: "point",
      voice: { style: "calm", speed: 0.95 },
    },
  ],
});

export class MockBackend {
  private handlers = new Set<MockGatewayEventHandler>();
  private timers: ReturnType<typeof setTimeout>[] = [];
  private connected = false;

  subscribe(handler: MockGatewayEventHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  connect(): void {
    if (this.connected) return;
    this.connected = true;
    this.emit({ type: "gateway.connected", at: new Date().toISOString() });
    this.scheduleDemo();
  }

  disconnect(): void {
    this.connected = false;
    this.timers.forEach((timer) => clearTimeout(timer));
    this.timers = [];
    this.emit({ type: "gateway.disconnected", at: new Date().toISOString() });
  }

  submitUserMessage(text: string): void {
    if (!this.connected) return;
    const sessionId = mockDesktopDefaults.sessionId;
    this.emit({
      type: "agent.message.started",
      at: new Date().toISOString(),
      sessionId,
    });

    const timer = setTimeout(() => {
      this.emit({
        type: "agent.message.completed",
        at: new Date().toISOString(),
        sessionId,
        displayPlan: planFromText(
          `收到：${text || "空消息"}。这条回复来自 mock backend。`,
          "happy",
        ),
      });
    }, mockDesktopDefaults.delays.messageResponseMs);
    this.timers.push(timer);
  }

  submitMockLlmResult(rawOutput: string): void {
    if (!this.connected) return;
    const sessionId = mockDesktopDefaults.sessionId;
    this.emit({
      type: "agent.message.started",
      at: new Date().toISOString(),
      sessionId,
    });

    const timer = setTimeout(() => {
      this.emit({
        type: "agent.message.completed",
        at: new Date().toISOString(),
        sessionId,
        displayPlan: planFromLlmOutput(rawOutput),
      });
    }, mockDesktopDefaults.delays.llmResultMs);
    this.timers.push(timer);
  }

  private scheduleDemo(): void {
    const sessionId = mockDesktopDefaults.sessionId;
    this.timers.push(
      setTimeout(() => {
        this.emit({
          type: "agent.message.started",
          at: new Date().toISOString(),
          sessionId,
        });
      }, mockDesktopDefaults.delays.demoStartedMs),
      setTimeout(() => {
        this.emit({
          type: "agent.message.completed",
          at: new Date().toISOString(),
          sessionId,
          displayPlan: demoPlan ?? planFromText("Mock backend 已连接。", "happy"),
        });
      }, mockDesktopDefaults.delays.demoCompletedMs),
    );
  }

  private emit(event: MockGatewayEvent): void {
    for (const handler of this.handlers) {
      handler(event);
    }
  }
}

export const mockBackend = new MockBackend();
