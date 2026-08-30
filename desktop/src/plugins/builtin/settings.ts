import {
  clearPersistedPomodoroSettings,
  readPersistedPomodoroSettings,
  sanitizePomodoroSettings,
} from "@/services/pomodoroSettingsStorage";
import { POMODORO_PLUGIN_ID } from "./pomodoro/manifest";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

/** Initial values and one-time migrations owned by bundled Desktop facets. */
export function createInitialBuiltinDesktopPluginSettings(): Record<
  string,
  unknown
> {
  return {
    [POMODORO_PLUGIN_ID]: readPersistedPomodoroSettings(),
  };
}

export function sanitizeBuiltinDesktopPluginSettings(
  value: unknown,
  fallback: Record<string, unknown> = {},
  legacyLocalConfig?: unknown,
): Record<string, unknown> {
  const record = isRecord(value) ? value : {};
  const legacy = isRecord(legacyLocalConfig) ? legacyLocalConfig : {};
  const pomodoro =
    record[POMODORO_PLUGIN_ID] ??
    legacy.pomodoro ??
    fallback[POMODORO_PLUGIN_ID];
  return {
    ...fallback,
    ...record,
    [POMODORO_PLUGIN_ID]: sanitizePomodoroSettings(pomodoro),
  };
}

export function clearLegacyBuiltinDesktopPluginSettings(): void {
  clearPersistedPomodoroSettings();
}
