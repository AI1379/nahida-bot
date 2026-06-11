import type { DisplayMotion } from "./displayPlan";
import type {
  Live2DExpressionMap,
  Live2DModelManifest,
  Live2DMotionTarget,
} from "./live2d";

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

export interface LocalDesktopConfig {
  selectedModelId: string;
  modelConfigs: Record<string, ModelMappingConfig>;
  windowState: DesktopWindowState;
  performanceMode: PerformanceMode;
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
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    edgeExposedPx: 42,
  };
}

export function createDefaultLocalDesktopConfig(
  manifest: Live2DModelManifest,
): LocalDesktopConfig {
  return {
    selectedModelId: manifest.id,
    modelConfigs: {
      [manifest.id]: modelMappingConfigFromManifest(manifest),
    },
    windowState: {
      width: 420,
      height: 620,
      x: null,
      y: null,
      edge: "right",
      exposedPx: 42,
      alwaysOnTop: true,
      clickThrough: true,
      interactionMode: "click_through",
    },
    performanceMode: "balanced",
  };
}
