import { pomodoroDefaults } from "@/domain/config";
import type { PomodoroSettings } from "@/domain/config";

const storageKey = "nahida.desktop.pomodoro.settings.v1";

function finiteDuration(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(minimum, Math.min(maximum, Math.round(value)))
    : fallback;
}

export function sanitizePomodoroSettings(value: unknown): PomodoroSettings {
  const record =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  return {
    enabled:
      typeof record.enabled === "boolean"
        ? record.enabled
        : pomodoroDefaults.enabled,
    workDurationMinutes: finiteDuration(
      record.workDurationMinutes,
      pomodoroDefaults.workDurationMinutes,
      1,
      120,
    ),
    breakDurationMinutes: finiteDuration(
      record.breakDurationMinutes,
      pomodoroDefaults.breakDurationMinutes,
      1,
      60,
    ),
    workStartText:
      typeof record.workStartText === "string" && record.workStartText.trim()
        ? record.workStartText.trim().slice(0, 200)
        : pomodoroDefaults.workStartText,
    breakStartText:
      typeof record.breakStartText === "string" && record.breakStartText.trim()
        ? record.breakStartText.trim().slice(0, 200)
        : pomodoroDefaults.breakStartText,
    breakEndText:
      typeof record.breakEndText === "string" && record.breakEndText.trim()
        ? record.breakEndText.trim().slice(0, 200)
        : pomodoroDefaults.breakEndText,
  };
}

export function readPersistedPomodoroSettings(): PomodoroSettings {
  if (typeof window === "undefined") return sanitizePomodoroSettings(null);
  try {
    const raw = window.localStorage.getItem(storageKey);
    return sanitizePomodoroSettings(raw ? JSON.parse(raw) : null);
  } catch {
    return sanitizePomodoroSettings(null);
  }
}

export function writePersistedPomodoroSettings(
  settings: PomodoroSettings,
): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    storageKey,
    JSON.stringify(sanitizePomodoroSettings(settings)),
  );
}
