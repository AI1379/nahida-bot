import type { DisplayPlan } from "@/domain/displayPlan";
import type { TranscriptEntry } from "./types";

export function systemTranscriptEntry(text: string, at: string): TranscriptEntry {
  return {
    id: crypto.randomUUID(),
    role: "system",
    text,
    at,
  };
}

export function userTranscriptEntry(text: string, at: string): TranscriptEntry {
  return {
    id: crypto.randomUUID(),
    role: "user",
    text,
    at,
  };
}

export function assistantTranscriptEntry(
  text: string,
  at: string,
  displayPlan: DisplayPlan,
): TranscriptEntry {
  return {
    id: crypto.randomUUID(),
    role: "assistant",
    text,
    at,
    displayPlan,
  };
}
