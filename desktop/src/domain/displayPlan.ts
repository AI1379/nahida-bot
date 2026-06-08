export type DisplayEmotion =
  | "neutral"
  | "happy"
  | "thinking"
  | "worried"
  | "error"
  | "offline";

export type DisplayMotion =
  | "idle"
  | "nod"
  | "point"
  | "wave"
  | "notify"
  | "speaking";

export interface VoicePlan {
  style?: "neutral" | "bright" | "calm" | "soft";
  speed?: number;
  pitch?: number;
}

export interface DisplaySegment {
  text: string;
  emotion?: DisplayEmotion;
  motion?: DisplayMotion;
  pauseAfterMs?: number;
  voice?: VoicePlan;
}

export interface DisplayPlan {
  version: "1.0";
  text: string;
  segments: DisplaySegment[];
}

const emotions = new Set<DisplayEmotion>([
  "neutral",
  "happy",
  "thinking",
  "worried",
  "error",
  "offline",
]);

const motions = new Set<DisplayMotion>([
  "idle",
  "nod",
  "point",
  "wave",
  "notify",
  "speaking",
]);

function cleanText(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.replace(/\s+/g, " ").trim();
}

function cleanNumber(
  value: unknown,
  fallback: number,
  min: number,
  max: number,
): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, value));
}

function normalizeSegment(value: unknown): DisplaySegment | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const text = cleanText(record.text);
  if (!text) return null;

  const emotion = emotions.has(record.emotion as DisplayEmotion)
    ? (record.emotion as DisplayEmotion)
    : undefined;
  const motion = motions.has(record.motion as DisplayMotion)
    ? (record.motion as DisplayMotion)
    : undefined;

  let voice: VoicePlan | undefined;
  if (record.voice && typeof record.voice === "object") {
    const rawVoice = record.voice as Record<string, unknown>;
    voice = {
      style: ["neutral", "bright", "calm", "soft"].includes(
        String(rawVoice.style),
      )
        ? (rawVoice.style as VoicePlan["style"])
        : undefined,
      speed: cleanNumber(rawVoice.speed, 1, 0.5, 1.5),
      pitch: cleanNumber(rawVoice.pitch, 0, -6, 6),
    };
  }

  return {
    text,
    emotion,
    motion,
    pauseAfterMs: cleanNumber(record.pauseAfterMs, 0, 0, 3000),
    voice,
  };
}

export function normalizeDisplayPlan(value: unknown): DisplayPlan | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const text = cleanText(record.text);
  if (!text) return null;

  const rawSegments = Array.isArray(record.segments) ? record.segments : [];
  const segments = rawSegments
    .slice(0, 12)
    .map(normalizeSegment)
    .filter((segment): segment is DisplaySegment => segment !== null);

  return {
    version: "1.0",
    text,
    segments: segments.length
      ? segments
      : [{ text, emotion: "neutral", motion: "speaking" }],
  };
}

export function planFromText(
  text: string,
  emotion: DisplayEmotion = "neutral",
): DisplayPlan {
  const clean = cleanText(text);
  return {
    version: "1.0",
    text: clean,
    segments: [{ text: clean, emotion, motion: "speaking" }],
  };
}
