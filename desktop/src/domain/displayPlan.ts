import {
  displayPlanPolicy,
  sanitizeExpressionKeyword as sanitizeExpressionKeywordValue,
} from "./displayPlanPolicy";

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
  | "speaking"
  | "emerge"
  | "retreat";

export interface VoicePlan {
  style?: "neutral" | "bright" | "calm" | "soft";
  speed?: number;
  pitch?: number;
}

export interface DisplaySegment {
  text: string;
  emotion?: DisplayEmotion;
  expression?: string;
  motion?: DisplayMotion;
  pauseAfterMs?: number;
  voice?: VoicePlan;
}

export interface DisplayPlan {
  version: "1.0";
  text: string;
  segments: DisplaySegment[];
}

type JsonRecord = Record<string, unknown>;

export const displayEmotions: DisplayEmotion[] = [
  "neutral",
  "happy",
  "thinking",
  "worried",
  "error",
  "offline",
];

export const displayMotions: DisplayMotion[] = [
  "idle",
  "nod",
  "point",
  "wave",
  "notify",
  "speaking",
  "emerge",
  "retreat",
];

const emotions = new Set<DisplayEmotion>(displayEmotions);
const motions = new Set<DisplayMotion>(displayMotions);

const voiceStyles = new Set<NonNullable<VoicePlan["style"]>>([
  "neutral",
  "bright",
  "calm",
  "soft",
]);

export function isDisplayEmotion(value: unknown): value is DisplayEmotion {
  return emotions.has(value as DisplayEmotion);
}

export function isDisplayMotion(value: unknown): value is DisplayMotion {
  return motions.has(value as DisplayMotion);
}

function cleanText(
  value: unknown,
  maxLength: number = displayPlanPolicy.maximumTextLength,
): string {
  if (typeof value !== "string") return "";
  return value.replace(/\s+/g, " ").trim().slice(0, maxLength);
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

function cleanEmotion(value: unknown): DisplayEmotion | undefined {
  return emotions.has(value as DisplayEmotion)
    ? (value as DisplayEmotion)
    : undefined;
}

function cleanExpressionKeyword(value: unknown): string | undefined {
  return sanitizeExpressionKeywordValue(value) || undefined;
}

function cleanMotion(value: unknown): DisplayMotion | undefined {
  return motions.has(value as DisplayMotion)
    ? (value as DisplayMotion)
    : undefined;
}

function cleanVoiceStyle(value: unknown): VoicePlan["style"] | undefined {
  return voiceStyles.has(value as NonNullable<VoicePlan["style"]>)
    ? (value as VoicePlan["style"])
    : undefined;
}

function normalizeSegment(value: unknown): DisplaySegment | null {
  if (!value || typeof value !== "object") return null;
  const record = value as JsonRecord;
  const text = cleanText(
    record.text,
    displayPlanPolicy.maximumSegmentTextLength,
  );
  if (!text) return null;

  const emotion = cleanEmotion(record.emotion);
  const expression =
    cleanExpressionKeyword(
      record.expression ?? record.expressionKeyword ?? record.expression_keyword,
    ) ?? (emotion ? undefined : cleanExpressionKeyword(record.emotion));
  const motion = cleanMotion(record.motion);

  let voice: VoicePlan | undefined;
  if (record.voice && typeof record.voice === "object") {
    const rawVoice = record.voice as JsonRecord;
    voice = {
      style: cleanVoiceStyle(rawVoice.style),
      speed: cleanNumber(
        rawVoice.speed,
        1,
        displayPlanPolicy.voiceSpeed.minimum,
        displayPlanPolicy.voiceSpeed.maximum,
      ),
      pitch: cleanNumber(
        rawVoice.pitch,
        0,
        displayPlanPolicy.voicePitch.minimum,
        displayPlanPolicy.voicePitch.maximum,
      ),
    };
  }

  return {
    text,
    emotion,
    expression,
    motion,
    pauseAfterMs: cleanNumber(
      record.pauseAfterMs ?? record.pause_after_ms,
      0,
      0,
      displayPlanPolicy.maximumPauseAfterMs,
    ),
    voice,
  };
}

function joinedSegmentText(values: unknown[]): string {
  return cleanText(
    values
      .map((value) =>
        value && typeof value === "object"
          ? cleanText(
              (value as JsonRecord).text,
              displayPlanPolicy.maximumSegmentTextLength,
            )
          : "",
      )
      .filter(Boolean)
      .join(" "),
  );
}

export function normalizeDisplayPlan(
  value: unknown,
  fallbackText = "",
): DisplayPlan | null {
  if (!value || typeof value !== "object") return null;
  const record = value as JsonRecord;

  const rawSegments = Array.isArray(record.segments) ? record.segments : [];
  const text =
    cleanText(record.text) ||
    joinedSegmentText(rawSegments) ||
    cleanText(fallbackText);
  if (!text) return null;

  const segments = rawSegments
    .slice(0, displayPlanPolicy.maximumSegments)
    .map(normalizeSegment)
    .filter((segment): segment is DisplaySegment => segment !== null);

  return {
    version: "1.0",
    text,
    segments: segments.length
      ? segments
      : [
          {
            text,
            emotion: cleanEmotion(record.emotion) ?? "neutral",
            expression: cleanExpressionKeyword(
              record.expression ??
                record.expressionKeyword ??
                record.expression_keyword,
            ),
            motion: cleanMotion(record.motion),
          },
        ],
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
    segments: [{ text: clean, emotion }],
  };
}

function tryParseJson(value: string): unknown | null {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function extractJsonCandidate(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) return fenced[1].trim();

  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    return trimmed;
  }

  const objectStart = trimmed.indexOf("{");
  const objectEnd = trimmed.lastIndexOf("}");
  if (objectStart >= 0 && objectEnd > objectStart) {
    return trimmed.slice(objectStart, objectEnd + 1);
  }

  return null;
}

function nestedRecord(value: unknown, keys: string[]): unknown {
  let cursor = value;
  for (const key of keys) {
    if (!cursor || typeof cursor !== "object") return undefined;
    cursor = (cursor as JsonRecord)[key];
  }
  return cursor;
}

function planFromStructuredOutput(value: unknown): DisplayPlan | null {
  if (Array.isArray(value)) {
    return normalizeDisplayPlan({ segments: value });
  }

  if (!value || typeof value !== "object") return null;
  const record = value as JsonRecord;
  const outerText = cleanText(record.text ?? record.content);

  for (const candidate of [
    record.display_plan,
    record.displayPlan,
    nestedRecord(record, ["metadata", "display_plan"]),
    nestedRecord(record, ["metadata", "displayPlan"]),
    nestedRecord(record, ["message", "display_plan"]),
    nestedRecord(record, ["message", "displayPlan"]),
  ]) {
    const plan = normalizeDisplayPlan(candidate, outerText);
    if (plan) return plan;
  }

  const content =
    nestedRecord(record, ["choices", "0", "message", "content"]) ??
    nestedRecord(record, ["message", "content"]) ??
    record.content;
  if (typeof content === "string" && cleanText(content)) {
    return planFromLlmOutput(content);
  }

  const direct = normalizeDisplayPlan(value);
  if (direct) return direct;

  return outerText ? planFromText(outerText, cleanEmotion(record.emotion)) : null;
}

export function planFromLlmOutput(rawOutput: string): DisplayPlan {
  const trimmed = rawOutput.trim();
  if (!trimmed) return planFromText("", "neutral");

  const jsonCandidate = extractJsonCandidate(trimmed);
  const parsed = jsonCandidate ? tryParseJson(jsonCandidate) : null;
  const parsedPlan = parsed === null ? null : planFromStructuredOutput(parsed);
  if (parsedPlan) return parsedPlan;

  return planFromText(trimmed, "neutral");
}
