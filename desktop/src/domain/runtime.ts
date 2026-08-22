import type { PerformanceMode } from "./config";
import type { DisplayEmotion, DisplayMotion, DisplayPlan } from "./displayPlan";

export type DesktopEventSource = "local" | "mock" | "gateway";
export type RenderMode = "suspended" | "idle" | "speaking" | "active";

export type PetRuntimeStatus =
  | "hidden"
  | "peek"
  | "emerging"
  | "emerged"
  | "speaking"
  | "chat"
  | "retreating"
  | "error";

export interface CapabilityExecutionError {
  code: string;
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export type CapabilityExecutionResult =
  | { ok: true; result: Record<string, unknown> }
  | { ok: false; error: CapabilityExecutionError };

interface DesktopEventBase {
  source: DesktopEventSource;
  at: string;
  sessionId?: string;
}

export type DesktopEvent =
  | (DesktopEventBase & {
      type: "connection.changed";
      connected: boolean;
      reason?: string;
      authRequired?: boolean;
      gatewayUrl?: string;
      nodeId?: string;
    })
  | (DesktopEventBase & {
      type: "message.started";
      sessionId: string;
    })
  | (DesktopEventBase & {
      type: "message.completed";
      sessionId: string;
      displayPlan: DisplayPlan;
    })
  | (DesktopEventBase & {
      type: "notification.error";
      message: string;
    })
  | (DesktopEventBase & {
      type: "notification.reminder";
      message: string;
      /** Requests spoken playback in addition to the normal queued reminder. */
      ttsEnabled?: boolean;
      /** Opaque key used to deduplicate identical reminders. */
      dedupeKey?: string;
    })
  | (DesktopEventBase & {
      type: "capability.invoked";
      invocationId: string;
      capability: string;
      arguments: Record<string, unknown>;
    })
  | (DesktopEventBase & {
      type: "user.message.submitted";
      source: "local";
      sessionId: string;
      text: string;
    });

export interface PresentationPlan {
  id: string;
  /** Local conversation turn correlated with this presentation, when known. */
  turnId?: string;
  source: DesktopEventSource;
  targetSessionId?: string;
  displayPlan: DisplayPlan;
  bubbleText: string;
  ttsEnabled: boolean;
  interruption: "replace" | "queue";
  /** Collision key for deduplication — identical keys within the queue window are merged. */
  dedupeKey?: string;
  createdAt: string;
}

export interface PetRuntimeState {
  status: PetRuntimeStatus;
  renderMode: RenderMode;
  emotion: DisplayEmotion;
  expressionKey: string;
  motion: DisplayMotion;
  speaking: boolean;
  currentSegmentIndex: number;
  /** Actual synthesized audio duration for the current segment when known. */
  segmentDurationMs: number | null;
  activePresentationId: string | null;
  bubbleText: string;
  clickThrough: boolean;
  interactionMode: "click_through" | "interactive";
  lastEventAt: string | null;
}

export function createInitialPetRuntimeState(): PetRuntimeState {
  return {
    status: "hidden",
    renderMode: "suspended",
    emotion: "neutral",
    expressionKey: "neutral",
    motion: "idle",
    speaking: false,
    currentSegmentIndex: 0,
    segmentDurationMs: null,
    activePresentationId: null,
    bubbleText: "",
    clickThrough: true,
    interactionMode: "click_through",
    lastEventAt: null,
  };
}

export function renderModeForPerformanceMode(
  performanceMode: PerformanceMode,
  speaking = false,
): RenderMode {
  if (speaking) return "speaking";
  // Power saver lowers the frame budget but must not freeze a visible pet;
  // "suspended" is reserved for hidden/minimized/locked states.
  if (performanceMode === "active") return "active";
  return "idle";
}
