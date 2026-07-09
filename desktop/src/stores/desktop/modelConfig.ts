import type {
  LocalDesktopConfig,
  ModelMappingConfig,
} from "@/domain/config";
import type { Live2DExpressionMap } from "@/domain/live2d";
import type {
  PersistedExpressionMaps,
  PersistedMotionMaps,
} from "@/services/modelMappingStorage";

export type ExpressionKeywordMap = Live2DExpressionMap;

export function withPersistedModelMappings(
  config: LocalDesktopConfig,
  persistedExpressions: PersistedExpressionMaps,
  persistedMotions: PersistedMotionMaps,
): LocalDesktopConfig {
  return {
    ...config,
    modelConfigs: Object.fromEntries(
      Object.entries(config.modelConfigs).map(([modelId, modelConfig]) => [
        modelId,
        {
          ...modelConfig,
          expressionMap: {
            ...modelConfig.expressionMap,
            ...(persistedExpressions[modelId] ?? {}),
          },
          motionMap: {
            ...modelConfig.motionMap,
            ...(persistedMotions[modelId] ?? {}),
          },
        },
      ]),
    ),
  };
}

export function withModelConfig(
  config: LocalDesktopConfig,
  modelConfig: ModelMappingConfig,
): LocalDesktopConfig {
  return {
    ...config,
    modelConfigs: {
      ...config.modelConfigs,
      [modelConfig.modelId]: modelConfig,
    },
  };
}

export function nextCustomExpressionKeyword(map: ExpressionKeywordMap): string {
  const base = "custom";
  if (!Object.prototype.hasOwnProperty.call(map, base)) return base;
  for (let index = 2; index < 100; index += 1) {
    const candidate = `${base}-${index}`;
    if (!Object.prototype.hasOwnProperty.call(map, candidate)) return candidate;
  }
  return `${base}-${Date.now()}`;
}
