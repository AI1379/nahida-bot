import type { DisplayMotion, DisplayPlan } from "@/domain/displayPlan";

export interface TranscriptEntry {
  id: string;
  role: "system" | "user" | "assistant";
  text: string;
  at: string;
  displayPlan?: DisplayPlan;
}

export type PendingAfterEmergeAction =
  | { type: "none" }
  | { type: "presentation" }
  | { type: "error" }
  | { type: "motion"; motion: DisplayMotion };

export interface PendingAfterEmerge {
  enterChat: boolean;
  action: PendingAfterEmergeAction;
}

export function createEmptyPendingAfterEmerge(): PendingAfterEmerge {
  return {
    enterChat: false,
    action: { type: "none" },
  };
}
