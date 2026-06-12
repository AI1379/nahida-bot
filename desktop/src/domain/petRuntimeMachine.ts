import type { PerformanceMode } from "./config";
import {
  renderModeForPerformanceMode,
  type PetRuntimeState,
  type PetRuntimeStatus,
} from "./runtime";

export type PetRuntimeSignal =
  | "peek"
  | "emerge"
  | "emerged"
  | "speak"
  | "finish_speaking"
  | "enter_chat"
  | "exit_chat"
  | "retreat"
  | "hide"
  | "fail";

const allowedTransitions: Record<
  PetRuntimeStatus,
  ReadonlySet<PetRuntimeSignal>
> = {
  // No "fail" while tucked away: a disconnect should not pop the full
  // window out of the edge. The store still updates emotion separately.
  hidden: new Set(["peek", "emerge"]),
  peek: new Set(["emerge", "retreat", "hide"]),
  emerging: new Set(["emerged", "speak", "enter_chat", "retreat", "fail"]),
  emerged: new Set([
    "peek",
    "speak",
    "enter_chat",
    "retreat",
    "fail",
  ]),
  speaking: new Set([
    "speak",
    "finish_speaking",
    "enter_chat",
    "retreat",
    "fail",
  ]),
  chat: new Set([
    "speak",
    "finish_speaking",
    "exit_chat",
    "retreat",
    "fail",
  ]),
  retreating: new Set(["peek", "emerge", "hide", "fail"]),
  error: new Set(["emerge", "enter_chat", "retreat", "hide"]),
};

function transitionPatch(
  status: PetRuntimeStatus,
  signal: PetRuntimeSignal,
  performanceMode: PerformanceMode,
): Partial<PetRuntimeState> {
  const inChat = status === "chat";

  switch (signal) {
    case "peek":
      return {
        status: "peek",
        renderMode: renderModeForPerformanceMode(performanceMode),
        speaking: false,
        clickThrough: true,
        interactionMode: "click_through",
      };
    case "emerge":
      return {
        status: "emerging",
        renderMode: "active",
        motion: "emerge",
        clickThrough: true,
        interactionMode: "click_through",
      };
    case "emerged":
      return {
        status: "emerged",
        renderMode: renderModeForPerformanceMode(performanceMode),
        motion: "idle",
        speaking: false,
        clickThrough: true,
        interactionMode: "click_through",
      };
    case "finish_speaking":
      // A reply finishing inside chat must not kick the user out of the
      // composer; only non-chat speech falls back to the emerged state.
      return inChat
        ? {
            status: "chat",
            renderMode: "active",
            motion: "idle",
            speaking: false,
            clickThrough: false,
            interactionMode: "interactive",
          }
        : {
            status: "emerged",
            renderMode: renderModeForPerformanceMode(performanceMode),
            motion: "idle",
            speaking: false,
            clickThrough: true,
            interactionMode: "click_through",
          };
    case "exit_chat":
      return {
        status: "emerged",
        renderMode: renderModeForPerformanceMode(performanceMode),
        motion: "idle",
        speaking: false,
        clickThrough: true,
        interactionMode: "click_through",
      };
    case "speak":
      return inChat
        ? {
            status: "chat",
            renderMode: "speaking",
            speaking: true,
            clickThrough: false,
            interactionMode: "interactive",
          }
        : {
            status: "speaking",
            renderMode: "speaking",
            speaking: true,
            clickThrough: true,
            interactionMode: "click_through",
          };
    case "enter_chat":
      return {
        status: "chat",
        renderMode: "active",
        clickThrough: false,
        interactionMode: "interactive",
      };
    case "retreat":
      return {
        status: "retreating",
        renderMode: "active",
        motion: "retreat",
        speaking: false,
        clickThrough: true,
        interactionMode: "click_through",
      };
    case "hide":
      return {
        status: "hidden",
        renderMode: "suspended",
        motion: "idle",
        speaking: false,
        clickThrough: true,
        interactionMode: "click_through",
      };
    case "fail":
      return {
        status: "error",
        renderMode: renderModeForPerformanceMode(performanceMode),
        motion: "idle",
        speaking: false,
        clickThrough: true,
        interactionMode: "click_through",
      };
  }
}

export function transitionPetRuntime(
  state: PetRuntimeState,
  signal: PetRuntimeSignal,
  performanceMode: PerformanceMode,
): PetRuntimeState {
  if (!allowedTransitions[state.status].has(signal)) return state;

  return {
    ...state,
    ...transitionPatch(state.status, signal, performanceMode),
    lastEventAt: new Date().toISOString(),
  };
}
