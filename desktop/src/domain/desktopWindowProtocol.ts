import type { LocalDesktopConfig } from "./config";
import type { DisplayPlan } from "./displayPlan";
import type { PetRuntimeState, PresentationPlan } from "./runtime";
import type { TurnRecord } from "./conversation";
import type { PomodoroState } from "@/services/pomodoroService";

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
  /**
   * Pomodoro timer state for the pet window badge. Only changes on phase
   * transitions; the remaining seconds are re-derived locally from
   * `expiresAt`, so this never republishes once per second.
   */
  pomodoro?: PomodoroState;
}

export type PetWindowCommand =
  | { type: "request_state" }
  | { type: "peek" }
  | { type: "emerge" }
  | { type: "retreat" }
  | { type: "hide" }
  | { type: "enter_chat" }
  | { type: "exit_chat" }
  | { type: "toggle_pomodoro" }
  | { type: "submit_message"; text: string }
  /** Cursor is over the visible pet; re-arms auto retreat / chat timeouts. */
  | { type: "pointer_activity" }
  /** The pet window finished its emerge/retreat slide animation. */
  | { type: "transition_done"; phase: "emerge" | "retreat" }
  /** The model was double-clicked while interactive; raise the main window. */
  | { type: "open_main_window" };
