import { createTypedStorage } from "@/utils/storage";
import { pomodoroDefaults } from "@/domain/config";
import type { PomodoroSettings } from "@/domain/config";

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
    totalRounds: finiteDuration(
      record.totalRounds,
      pomodoroDefaults.totalRounds,
      1,
      16,
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
    roundsDoneText:
      typeof record.roundsDoneText === "string" && record.roundsDoneText.trim()
        ? record.roundsDoneText.trim().slice(0, 200)
        : pomodoroDefaults.roundsDoneText,
    speakReminders:
      typeof record.speakReminders === "boolean"
        ? record.speakReminders
        : pomodoroDefaults.speakReminders,
    dynamicText:
      typeof record.dynamicText === "boolean"
        ? record.dynamicText
        : pomodoroDefaults.dynamicText,
  };
}

const storage = createTypedStorage<PomodoroSettings>(
  "nahida.desktop.pomodoro.settings.v1",
  sanitizePomodoroSettings,
);

export const readPersistedPomodoroSettings = storage.read;
export const writePersistedPomodoroSettings = storage.write;
export const clearPersistedPomodoroSettings = storage.clear;
