import {
  displayPlanPolicy,
  sanitizeExpressionKeyword,
  sanitizeExpressionName,
} from "@/domain/displayPlan";
import { isDisplayMotion } from "@/domain/displayPlan";
import type { DisplayMotion } from "@/domain/displayPlan";
import type {
  Live2DExpressionMap,
  Live2DMotionTarget,
} from "@/domain/live2d";

export type MotionMap = Partial<Record<DisplayMotion, Live2DMotionTarget>>;
export type PersistedExpressionMaps = Record<string, Live2DExpressionMap>;
export type PersistedMotionMaps = Record<string, MotionMap>;

const storageKeys = {
  expressionMap: "nahida.desktop.live2d.expressionMap.v2",
  legacyExpressionMap: "nahida.desktop.live2d.emotionMap.v1",
  motionMap: "nahida.desktop.live2d.motionMap.v1",
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function uniqueExpressionNames(value: unknown): string[] {
  const values = Array.isArray(value) ? value : [value];
  const names = values.map(sanitizeExpressionName).filter(Boolean);
  return [...new Set(names)];
}

export function sanitizeExpressionMap(value: unknown): Live2DExpressionMap {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([rawKeyword, rawExpressions]) => {
      const keyword = sanitizeExpressionKeyword(rawKeyword);
      if (!keyword) return [];
      if (rawExpressions === "") return [[keyword, []]];

      const expressions = uniqueExpressionNames(rawExpressions);
      return expressions.length || Array.isArray(rawExpressions)
        ? [[keyword, expressions]]
        : [];
    }),
  ) as Live2DExpressionMap;
}

export function sanitizeMotionTarget(
  value: unknown,
): Live2DMotionTarget | null {
  if (!isRecord(value)) return null;
  if (value.source === "none") return { source: "none" };
  if (value.source === "procedural" && isDisplayMotion(value.motion)) {
    return { source: "procedural", motion: value.motion };
  }

  const source = value.source ?? "model";
  if (
    source === "model" &&
    typeof value.group === "string" &&
    typeof value.index === "number" &&
    Number.isInteger(value.index) &&
    value.index >= 0
  ) {
    return {
      source: "model",
      group: value.group
        .trim()
        .slice(0, displayPlanPolicy.maximumMotionGroupLength),
      index: value.index,
    };
  }
  return null;
}

export function sanitizeMotionMap(value: unknown): MotionMap {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([rawMotion, rawTarget]) => {
      if (!isDisplayMotion(rawMotion)) return [];
      const target = sanitizeMotionTarget(rawTarget);
      return target ? [[rawMotion, target]] : [];
    }),
  ) as MotionMap;
}

export function readPersistedExpressionMaps(): PersistedExpressionMaps {
  if (typeof window === "undefined") return {};
  try {
    const raw =
      window.localStorage.getItem(storageKeys.expressionMap) ??
      window.localStorage.getItem(storageKeys.legacyExpressionMap);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).map(([modelId, value]) => [
        modelId,
        sanitizeExpressionMap(value),
      ]),
    );
  } catch {
    return {};
  }
}

export function writePersistedExpressionMap(
  modelId: string,
  expressionMap: Live2DExpressionMap,
): void {
  if (typeof window === "undefined") return;
  const persisted = readPersistedExpressionMaps();
  persisted[modelId] = sanitizeExpressionMap(expressionMap);
  window.localStorage.setItem(
    storageKeys.expressionMap,
    JSON.stringify(persisted),
  );
}

export function readPersistedMotionMaps(): PersistedMotionMaps {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKeys.motionMap);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).map(([modelId, value]) => [
        modelId,
        sanitizeMotionMap(value),
      ]),
    );
  } catch {
    return {};
  }
}

export function writePersistedMotionMap(
  modelId: string,
  motionMap: MotionMap,
): void {
  if (typeof window === "undefined") return;
  const persisted = readPersistedMotionMaps();
  persisted[modelId] = sanitizeMotionMap(motionMap);
  window.localStorage.setItem(storageKeys.motionMap, JSON.stringify(persisted));
}

export function clearPersistedModelMappings(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(storageKeys.expressionMap);
  window.localStorage.removeItem(storageKeys.legacyExpressionMap);
  window.localStorage.removeItem(storageKeys.motionMap);
}
