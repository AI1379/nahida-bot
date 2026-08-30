import { invoke, isTauri } from "@tauri-apps/api/core";
import { load, type Store } from "@tauri-apps/plugin-store";

import type {
  LocalDesktopConfig,
  ModelMappingConfig,
  PerformanceMode,
  PetTriggerSettings,
  PetWindowEdge,
} from "@/domain/config";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import { sanitizeGatewayConnectionSettings } from "@/domain/gatewayConnection";
import { sanitizeModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import { sanitizeBuiltinDesktopPluginSettings } from "@/plugins/builtin/settings";
import { sanitizeExpressionMap, sanitizeMotionMap } from "./modelMappingStorage";
import { sanitizeTtsSettings } from "./ttsSettingsStorage";

const STORE_FILE = "desktop-settings.json";
const STORE_KEY = "settings-v1";
const BROWSER_STORAGE_KEY = "nahida.desktop.settings.v1";

export interface SecureTokens {
  nodeToken: string;
  adminBearerToken: string;
}

export interface PersistedDesktopSettings {
  version: 1;
  localConfig: LocalDesktopConfig;
  pluginSettings: Record<string, unknown>;
  gatewayConnection: GatewayConnectionSettings;
}

let storePromise: Promise<Store> | null = null;
let writeQueue: Promise<void> = Promise.resolve();
let secureWriteQueue: Promise<void> = Promise.resolve();

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

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

function cleanString(value: unknown, fallback: string, maximum = 512): string {
  return typeof value === "string"
    ? value.trim().slice(0, maximum)
    : fallback;
}

function sanitizeCoordinate(
  value: unknown,
  fallback: number | null,
): number | null {
  if (value === null) return null;
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(-10000, Math.min(10000, value))
    : fallback;
}

function sanitizeModelConfig(
  value: unknown,
  fallback: ModelMappingConfig,
): ModelMappingConfig {
  if (!isRecord(value)) {
    return {
      ...fallback,
      expressionMap: { ...fallback.expressionMap },
      motionMap: { ...fallback.motionMap },
      lipSync: {
        ...fallback.lipSync,
        parameterIds: [...fallback.lipSync.parameterIds],
      },
    };
  }
  const lipSync = isRecord(value.lipSync) ? value.lipSync : {};
  const parameterIds = Array.isArray(lipSync.parameterIds)
    ? [...new Set(
        lipSync.parameterIds
          .filter((item): item is string => typeof item === "string")
          .map((item) => item.trim().slice(0, 96))
          .filter(Boolean),
      )].slice(0, 16)
    : [...fallback.lipSync.parameterIds];

  return {
    modelId: fallback.modelId,
    expressionMap:
      value.expressionMap === undefined
        ? { ...fallback.expressionMap }
        : sanitizeExpressionMap(value.expressionMap),
    motionMap:
      value.motionMap === undefined
        ? { ...fallback.motionMap }
        : sanitizeMotionMap(value.motionMap),
    lipSync: {
      enabled:
        typeof lipSync.enabled === "boolean"
          ? lipSync.enabled
          : fallback.lipSync.enabled,
      parameterIds,
    },
    scale: finiteNumber(value.scale, fallback.scale, 0.1, 4),
    offsetX: finiteNumber(value.offsetX, fallback.offsetX, -4000, 4000),
    offsetY: finiteNumber(value.offsetY, fallback.offsetY, -4000, 4000),
    edgeExposedPx: finiteNumber(
      value.edgeExposedPx,
      fallback.edgeExposedPx,
      8,
      240,
    ),
    performanceProfile: sanitizeModelPerformanceProfile(
      value.performanceProfile,
      fallback.performanceProfile,
    ),
  };
}

export function sanitizePetTriggerSettings(
  value: unknown,
  fallback: PetTriggerSettings,
): PetTriggerSettings {
  if (!isRecord(value)) return { ...fallback };
  const wakeDistancePx = finiteNumber(
    value.wakeDistancePx,
    fallback.wakeDistancePx,
    8,
    240,
  );
  return {
    wakeDistancePx,
    // Keep the hysteresis gap positive: hide must trigger farther away
    // than wake, or a peeking pet would flip straight back to hidden.
    hideDistancePx: Math.max(
      wakeDistancePx + 1,
      finiteNumber(value.hideDistancePx, fallback.hideDistancePx, 8, 480),
    ),
    autoRetreatMs: finiteNumber(
      value.autoRetreatMs,
      fallback.autoRetreatMs,
      1000,
      600000,
    ),
    chatIdleTimeoutMs: finiteNumber(
      value.chatIdleTimeoutMs,
      fallback.chatIdleTimeoutMs,
      5000,
      3600000,
    ),
  };
}

export function sanitizeLocalDesktopConfig(
  value: unknown,
  fallback: LocalDesktopConfig,
): LocalDesktopConfig {
  const record = isRecord(value) ? value : {};
  const rawModelConfigs = isRecord(record.modelConfigs)
    ? record.modelConfigs
    : {};
  const modelConfigs = Object.fromEntries(
    Object.entries(fallback.modelConfigs).map(([modelId, modelConfig]) => [
      modelId,
      sanitizeModelConfig(rawModelConfigs[modelId], modelConfig),
    ]),
  );
  const requestedModelId = cleanString(
    record.selectedModelId,
    fallback.selectedModelId,
    128,
  );
  const rawWindow = isRecord(record.windowState) ? record.windowState : {};
  const edge: PetWindowEdge =
    rawWindow.edge === "left" ||
    rawWindow.edge === "right" ||
    rawWindow.edge === "top" ||
    rawWindow.edge === "bottom"
      ? rawWindow.edge
      : fallback.windowState.edge;
  const performanceMode: PerformanceMode =
    record.performanceMode === "power_saver" ||
    record.performanceMode === "balanced" ||
    record.performanceMode === "active"
      ? record.performanceMode
      : fallback.performanceMode;

  return {
    selectedModelId: Object.hasOwn(modelConfigs, requestedModelId)
      ? requestedModelId
      : fallback.selectedModelId,
    modelConfigs,
    windowState: {
      width: finiteNumber(rawWindow.width, fallback.windowState.width, 160, 2400),
      height: finiteNumber(rawWindow.height, fallback.windowState.height, 200, 2400),
      x: sanitizeCoordinate(rawWindow.x, fallback.windowState.x),
      y: sanitizeCoordinate(rawWindow.y, fallback.windowState.y),
      edge,
      exposedPx: finiteNumber(
        rawWindow.exposedPx,
        fallback.windowState.exposedPx,
        8,
        240,
      ),
      alwaysOnTop:
        typeof rawWindow.alwaysOnTop === "boolean"
          ? rawWindow.alwaysOnTop
          : fallback.windowState.alwaysOnTop,
      clickThrough:
        typeof rawWindow.clickThrough === "boolean"
          ? rawWindow.clickThrough
          : fallback.windowState.clickThrough,
      interactionMode:
        rawWindow.interactionMode === "interactive" ||
        rawWindow.interactionMode === "click_through"
          ? rawWindow.interactionMode
          : fallback.windowState.interactionMode,
    },
    performanceMode,
    ttsSettings:
      record.ttsSettings === undefined
        ? { ...fallback.ttsSettings }
        : sanitizeTtsSettings(record.ttsSettings),
    petTriggers:
      record.petTriggers === undefined
        ? { ...fallback.petTriggers }
        : sanitizePetTriggerSettings(record.petTriggers, fallback.petTriggers),
    motionDataCollectionEnabled:
      typeof record.motionDataCollectionEnabled === "boolean"
        ? record.motionDataCollectionEnabled
        : fallback.motionDataCollectionEnabled,
  };
}

export function sanitizeDesktopPluginSettings(
  value: unknown,
  fallback: Record<string, unknown> = {},
  legacyLocalConfig?: unknown,
): Record<string, unknown> {
  return sanitizeBuiltinDesktopPluginSettings(
    value,
    fallback,
    legacyLocalConfig,
  );
}

export function withoutSecureTokens(
  settings: GatewayConnectionSettings,
): GatewayConnectionSettings {
  return {
    ...sanitizeGatewayConnectionSettings(settings),
    nodeToken: "",
    adminBearerToken: "",
  };
}

export function createPersistedDesktopSettings(
  localConfig: LocalDesktopConfig,
  gatewayConnection: GatewayConnectionSettings,
  pluginSettings: Record<string, unknown>,
): PersistedDesktopSettings {
  return {
    version: 1,
    localConfig: sanitizeLocalDesktopConfig(localConfig, localConfig),
    pluginSettings: sanitizeDesktopPluginSettings(pluginSettings),
    gatewayConnection: withoutSecureTokens(gatewayConnection),
  };
}

function sanitizePersistedSettings(
  value: unknown,
  fallbackLocalConfig: LocalDesktopConfig,
  fallbackGatewayConnection: GatewayConnectionSettings,
  fallbackPluginSettings: Record<string, unknown>,
): PersistedDesktopSettings | null {
  if (!isRecord(value)) return null;
  const rawLocalConfig = isRecord(value.localConfig) ? value.localConfig : {};
  return {
    version: 1,
    localConfig: sanitizeLocalDesktopConfig(
      value.localConfig,
      fallbackLocalConfig,
    ),
    pluginSettings: sanitizeDesktopPluginSettings(
      value.pluginSettings,
      fallbackPluginSettings,
      rawLocalConfig,
    ),
    gatewayConnection: withoutSecureTokens(
      isRecord(value.gatewayConnection)
        ? sanitizeGatewayConnectionSettings(value.gatewayConnection)
        : fallbackGatewayConnection,
    ),
  };
}

async function desktopStore(): Promise<Store> {
  storePromise ??= load(STORE_FILE, { autoSave: false });
  return storePromise;
}

function readBrowserSettings(): unknown {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(BROWSER_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function readDesktopSettings(
  fallbackLocalConfig: LocalDesktopConfig,
  fallbackGatewayConnection: GatewayConnectionSettings,
  fallbackPluginSettings: Record<string, unknown>,
): Promise<PersistedDesktopSettings | null> {
  const raw = isTauri()
    ? await (await desktopStore()).get<unknown>(STORE_KEY)
    : readBrowserSettings();
  return sanitizePersistedSettings(
    raw,
    fallbackLocalConfig,
    fallbackGatewayConnection,
    fallbackPluginSettings,
  );
}

export function writeDesktopSettings(
  localConfig: LocalDesktopConfig,
  gatewayConnection: GatewayConnectionSettings,
  pluginSettings: Record<string, unknown>,
): Promise<void> {
  const snapshot = createPersistedDesktopSettings(
    localConfig,
    gatewayConnection,
    pluginSettings,
  );
  writeQueue = writeQueue.catch(() => undefined).then(async () => {
    if (isTauri()) {
      const store = await desktopStore();
      await store.set(STORE_KEY, snapshot);
      await store.save();
      return;
    }
    if (typeof window !== "undefined") {
      window.localStorage.setItem(BROWSER_STORAGE_KEY, JSON.stringify(snapshot));
    }
  });
  return writeQueue;
}

export async function readSecureTokens(): Promise<SecureTokens> {
  if (!isTauri()) return { nodeToken: "", adminBearerToken: "" };
  return invoke<SecureTokens>("secure_tokens_read");
}

export async function writeSecureTokens(tokens: SecureTokens): Promise<void> {
  if (!isTauri()) return;
  const snapshot = { ...tokens };
  secureWriteQueue = secureWriteQueue
    .catch(() => undefined)
    .then(() => invoke("secure_tokens_write", { tokens: snapshot }));
  await secureWriteQueue;
}
