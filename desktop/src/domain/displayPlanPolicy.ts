export const displayPlanPolicy = {
  maximumTextLength: 4000,
  maximumSegmentTextLength: 800,
  maximumSegments: 12,
  maximumExpressionKeywordLength: 48,
  maximumExpressionNameLength: 160,
  maximumMotionGroupLength: 120,
  maximumPauseAfterMs: 3000,
  voiceSpeed: {
    minimum: 0.5,
    maximum: 1.5,
  },
  voicePitch: {
    minimum: -6,
    maximum: 6,
  },
} as const;

const expressionKeywordPattern = /^[\p{L}\p{N}_.-]+$/u;

export function sanitizeExpressionKeyword(value: unknown): string {
  if (typeof value !== "string") return "";
  const keyword = value
    .trim()
    .slice(0, displayPlanPolicy.maximumExpressionKeywordLength);
  return expressionKeywordPattern.test(keyword) ? keyword : "";
}

export function sanitizeExpressionName(value: unknown): string {
  return typeof value === "string"
    ? value.trim().slice(0, displayPlanPolicy.maximumExpressionNameLength)
    : "";
}
