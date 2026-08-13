export const motionIntentNames = [
  "idle",
  "greet",
  "thinking",
  "explain",
  "agree",
  "deny",
  "surprised",
  "concerned",
  "celebrate",
  "apology",
  "error",
  "retreat",
  "emerge",
] as const;

export type MotionIntentName = (typeof motionIntentNames)[number];

export const motionIntentSources = [
  "rule",
  "embedding",
  "llm",
  "cache",
  "manual",
] as const;

export type MotionIntentSource = (typeof motionIntentSources)[number];

export const motionEmotions = [
  "neutral",
  "happy",
  "thinking",
  "worried",
  "surprised",
  "error",
  "offline",
] as const;

export type MotionEmotion = (typeof motionEmotions)[number];

export type MotionCommunicativeAct =
  | "answer"
  | "ask"
  | "confirm"
  | "reject"
  | "search"
  | "warn"
  | "comfort";

export type MotionGaze =
  | "user"
  | "down-left"
  | "down-right"
  | "side"
  | "none";

export type MotionPriority =
  | "idle"
  | "background"
  | "speech"
  | "state-transition"
  | "critical";

/** Semantic performance request. It must not contain model parameter IDs. */
export interface MotionIntent {
  id: string;
  source: MotionIntentSource;
  intent: MotionIntentName;
  emotion: MotionEmotion;
  communicativeAct?: MotionCommunicativeAct;
  durationMs: number;
  /** Normalized requested energy in the inclusive range 0..1. */
  intensity: number;
  gaze?: MotionGaze;
  loopable: boolean;
  interruptible: boolean;
  priority: MotionPriority;
  tags?: string[];
}
