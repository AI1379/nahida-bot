import type { DisplayMotion } from "./displayPlan";
import type {
  Live2DExpressionMap,
  Live2DModelManifest,
  Live2DMotionTarget,
} from "./live2d";
import {
  desktopWindowDefaults,
  ttsDefaults,
} from "@/config/desktopRuntimeDefaults";

export type PerformanceMode = "power_saver" | "balanced" | "active";
export type PetWindowEdge = "left" | "right" | "top" | "bottom";
export type InteractionMode = "click_through" | "interactive";

export interface DesktopWindowState {
  width: number;
  height: number;
  x: number | null;
  y: number | null;
  edge: PetWindowEdge;
  exposedPx: number;
  alwaysOnTop: boolean;
  clickThrough: boolean;
  interactionMode: InteractionMode;
}

export interface ModelMappingConfig {
  modelId: string;
  expressionMap: Live2DExpressionMap;
  motionMap: Partial<Record<DisplayMotion, Live2DMotionTarget>>;
  lipSync: {
    enabled: boolean;
    parameterIds: string[];
  };
  scale: number;
  offsetX: number;
  offsetY: number;
  edgeExposedPx: number;
}

export interface TtsSettings {
  language: string;
  voiceUri: string;
  preferFemale: boolean;
  rate: number;
  pitch: number;
  volume: number;
}

export interface LocalDesktopConfig {
  selectedModelId: string;
  modelConfigs: Record<string, ModelMappingConfig>;
  windowState: DesktopWindowState;
  performanceMode: PerformanceMode;
  ttsSettings: TtsSettings;
}

export function modelMappingConfigFromManifest(
  manifest: Live2DModelManifest,
): ModelMappingConfig {
  return {
    modelId: manifest.id,
    expressionMap: { ...manifest.emotionMap },
    motionMap: { ...manifest.motionMap },
    lipSync: {
      enabled: manifest.lipSync.enabled,
      parameterIds: [...manifest.lipSync.parameterIds],
    },
    scale: manifest.layout.scale,
    offsetX: manifest.layout.offsetX,
    offsetY: manifest.layout.offsetY,
    edgeExposedPx: manifest.layout.edgeExposedPx,
  };
}

export function createDefaultLocalDesktopConfig(
  manifest: Live2DModelManifest,
  manifests: Live2DModelManifest[] = [manifest],
): LocalDesktopConfig {
  return {
    selectedModelId: manifest.id,
    modelConfigs: Object.fromEntries(
      manifests.map((candidate) => [
        candidate.id,
        modelMappingConfigFromManifest(candidate),
      ]),
    ),
    windowState: {
      width: desktopWindowDefaults.width,
      height: desktopWindowDefaults.height,
      x: null,
      y: null,
      edge: desktopWindowDefaults.edge,
      exposedPx: desktopWindowDefaults.exposedPx,
      alwaysOnTop: desktopWindowDefaults.alwaysOnTop,
      clickThrough: desktopWindowDefaults.clickThrough,
      interactionMode: desktopWindowDefaults.interactionMode,
    },
    performanceMode: desktopWindowDefaults.performanceMode,
    ttsSettings: { ...ttsDefaults },
  };
}

export function configuredModelFromManifest(
  manifest: Live2DModelManifest,
  config: ModelMappingConfig,
): Live2DModelManifest {
  return {
    ...manifest,
    emotionMap: { ...config.expressionMap },
    motionMap: { ...config.motionMap },
    lipSync: {
      enabled: config.lipSync.enabled,
      parameterIds: [...config.lipSync.parameterIds],
    },
    layout: {
      scale: config.scale,
      offsetX: config.offsetX,
      offsetY: config.offsetY,
      edgeExposedPx: config.edgeExposedPx,
    },
  };
}
