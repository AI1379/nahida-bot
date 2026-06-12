import { ttsDefaults } from "@/config/desktopRuntimeDefaults";
import type { TtsSettings } from "@/domain/config";

const storageKey = "nahida.desktop.tts.settings.v1";

function finiteNumber(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(minimum, Math.min(maximum, value))
    : fallback;
}

export function sanitizeTtsSettings(value: unknown): TtsSettings {
  const record =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  return {
    language:
      typeof record.language === "string" && record.language.trim()
        ? record.language.trim().slice(0, 32)
        : ttsDefaults.language,
    voiceUri:
      typeof record.voiceUri === "string"
        ? record.voiceUri.trim().slice(0, 512)
        : "",
    preferFemale:
      typeof record.preferFemale === "boolean"
        ? record.preferFemale
        : ttsDefaults.preferFemale,
    rate: finiteNumber(record.rate, ttsDefaults.rate, 0.5, 1.5),
    pitch: finiteNumber(record.pitch, ttsDefaults.pitch, -6, 6),
    volume: finiteNumber(record.volume, ttsDefaults.volume, 0, 1),
  };
}

export function readPersistedTtsSettings(): TtsSettings {
  if (typeof window === "undefined") return sanitizeTtsSettings(null);
  try {
    const raw = window.localStorage.getItem(storageKey);
    return sanitizeTtsSettings(raw ? JSON.parse(raw) : null);
  } catch {
    return sanitizeTtsSettings(null);
  }
}

export function writePersistedTtsSettings(settings: TtsSettings): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    storageKey,
    JSON.stringify(sanitizeTtsSettings(settings)),
  );
}
