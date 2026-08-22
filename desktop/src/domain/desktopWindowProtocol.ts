import type { LocalDesktopConfig } from "./config";
import type { DisplayPlan } from "./displayPlan";
import type { PetRuntimeState, PresentationPlan } from "./runtime";
import type { TurnRecord } from "./conversation";

export interface DesktopRuntimeSnapshot {
  connected: boolean;
  sessionId: string;
  activePlan: DisplayPlan | null;
  activePresentation: PresentationPlan | null;
  petRuntime: PetRuntimeState;
  localConfig: LocalDesktopConfig;
  localConfigVersion: number;
  expressionMapVersion: number;
  motionMapVersion: number;
  turns?: TurnRecord[];
  activeMotionFeedbackPlaybackId?: string | null;
}

export type PetWindowCommand =
  | { type: "request_state" }
  | { type: "peek" }
  | { type: "emerge" }
  | { type: "retreat" }
  | { type: "hide" }
  | { type: "enter_chat" }
  | { type: "exit_chat" }
  | { type: "submit_message"; text: string }
  /** Cursor is over the visible pet; re-arms auto retreat / chat timeouts. */
  | { type: "pointer_activity" }
  /** The pet window finished its emerge/retreat slide animation. */
  | { type: "transition_done"; phase: "emerge" | "retreat" }
  /** The model was double-clicked while interactive; raise the main window. */
  | { type: "open_main_window" };
