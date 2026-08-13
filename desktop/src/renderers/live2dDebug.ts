import type {
  Live2DExpressionOption,
  Live2DModelManifest,
  Live2DMotionOption,
} from "@/domain/live2d";
import {
  displayMotionPrimitiveMap,
  motionPrimitiveNames,
} from "@/domain/motionPrimitives";
import type { NormalizedPoseChannel } from "@/domain/normalizedPose";

import { live2dParameterIdsByPoseChannel } from "./live2dRetargeting";

export interface Live2DParameterDebugInfo {
  index: number;
  id: string;
  value: number;
  minimum: number;
  maximum: number;
  defaultValue: number;
  overridden: boolean;
  runtimeOverridden: boolean;
}

export interface Live2DKeyParameterDebugInfo
  extends Live2DParameterDebugInfo {
  channels: NormalizedPoseChannel[];
  lipSync: boolean;
}

export interface Live2DPartDebugInfo {
  index: number;
  id: string;
  opacity: number;
  overridden: boolean;
}

export interface Live2DDrawableDebugInfo {
  index: number;
  id: string;
  opacity: number;
  visible: boolean;
  renderOrder: number;
  textureIndex: number;
  vertexCount: number;
  bounds: {
    x: number;
    y: number;
    width: number;
    height: number;
    area: number;
  } | null;
}

export interface Live2DExpressionDebugInfo extends Live2DExpressionOption {}

export interface Live2DMotionDebugInfo extends Live2DMotionOption {}

export interface Live2DDebugSnapshot {
  modelName: string;
  expressions: Live2DExpressionDebugInfo[];
  nativeMotions: Live2DMotionDebugInfo[];
  proceduralMotions: Live2DMotionDebugInfo[];
  motions: Live2DMotionDebugInfo[];
  keyParameters: Live2DKeyParameterDebugInfo[];
  parameters: Live2DParameterDebugInfo[];
  parts: Live2DPartDebugInfo[];
  drawables: Live2DDrawableDebugInfo[];
}

export interface CubismCoreModelDebugApi {
  getModel?: () => unknown;
  getParameterCount?: () => number;
  getParameterMinimumValue?: (index: number) => number;
  getParameterMaximumValue?: (index: number) => number;
  getParameterDefaultValue?: (index: number) => number;
  getParameterValueByIndex?: (index: number) => number;
  setParameterValueByIndex?: (
    index: number,
    value: number,
    weight?: number,
  ) => void;
  getPartCount?: () => number;
  getPartOpacityByIndex?: (index: number) => number;
  setPartOpacityByIndex?: (index: number, opacity: number) => void;
  getDrawableCount?: () => number;
  getDrawableId?: (index: number) => string;
  getDrawableOpacity?: (index: number) => number;
  getDrawableDynamicFlagIsVisible?: (index: number) => boolean;
  getDrawableRenderOrders?: () => ArrayLike<number>;
  getDrawableTextureIndices?: (index: number) => number;
  getDrawableVertexCount?: (index: number) => number;
  getDrawableVertexPositions?: (index: number) => ArrayLike<number>;
  getDrawableVertices?: (index: number) => ArrayLike<number>;
  _parameterIds?: unknown;
  _partIds?: unknown;
}

export interface ModelSettingsDebugApi {
  expressions?: unknown[];
  motions?: Record<string, unknown[]>;
  json?: {
    FileReferences?: {
      Expressions?: unknown[];
      Motions?: Record<string, unknown[]>;
    };
  };
}

export interface DebugOverride {
  value: number;
  original: number;
}

export function createLive2DDebugSnapshot(options: {
  coreModel: CubismCoreModelDebugApi;
  settings: ModelSettingsDebugApi | null;
  manifest: Live2DModelManifest;
  isParameterOverridden: (index: number) => boolean;
  isRuntimeParameterOverridden: (index: number) => boolean;
  isPartOpacityOverridden: (index: number) => boolean;
}): Live2DDebugSnapshot {
  const {
    coreModel,
    settings,
    manifest,
    isParameterOverridden,
    isRuntimeParameterOverridden,
    isPartOpacityOverridden,
  } = options;
  const parameterIds = readLive2DIdList(coreModel._parameterIds);
  const partIds = readLive2DIdList(coreModel._partIds);
  const renderOrders = coreModel.getDrawableRenderOrders?.();

  const parameters = Array.from(
    { length: coreModel.getParameterCount?.() ?? 0 },
    (_, index) => ({
      index,
      id: live2DIdAt(parameterIds, index, `Parameter #${index}`),
      value: coreModel.getParameterValueByIndex?.(index) ?? 0,
      minimum: coreModel.getParameterMinimumValue?.(index) ?? 0,
      maximum: coreModel.getParameterMaximumValue?.(index) ?? 1,
      defaultValue: coreModel.getParameterDefaultValue?.(index) ?? 0,
      overridden: isParameterOverridden(index),
      runtimeOverridden: isRuntimeParameterOverridden(index),
    }),
  );

  const parts = Array.from(
    { length: coreModel.getPartCount?.() ?? 0 },
    (_, index) => ({
      index,
      id: live2DIdAt(partIds, index, `Part #${index}`),
      opacity: coreModel.getPartOpacityByIndex?.(index) ?? 1,
      overridden: isPartOpacityOverridden(index),
    }),
  );

  const drawables = Array.from(
    { length: coreModel.getDrawableCount?.() ?? 0 },
    (_, index) => ({
      index,
      id: coreModel.getDrawableId?.(index) ?? `Drawable #${index}`,
      opacity: coreModel.getDrawableOpacity?.(index) ?? 1,
      visible: coreModel.getDrawableDynamicFlagIsVisible?.(index) ?? true,
      renderOrder: renderOrders?.[index] ?? index,
      textureIndex: coreModel.getDrawableTextureIndices?.(index) ?? 0,
      vertexCount: coreModel.getDrawableVertexCount?.(index) ?? 0,
      bounds: getDrawableBounds(coreModel, index),
    }),
  ).sort(
    (left, right) => (right.bounds?.area ?? 0) - (left.bounds?.area ?? 0),
  );

  const nativeMotions = getNativeMotionDebugInfo(settings);
  const proceduralMotions = getProceduralMotionDebugInfo();

  return {
    modelName: manifest.name,
    expressions: getExpressionDebugInfo(settings),
    nativeMotions,
    proceduralMotions,
    motions: [...nativeMotions, ...proceduralMotions],
    keyParameters: getKeyParameterDebugInfo(parameters, manifest),
    parameters,
    parts,
    drawables,
  };
}

export function getProceduralMotionDebugInfo(): Live2DMotionDebugInfo[] {
  return motionPrimitiveNames.map((primitive, index) => ({
    source: "procedural",
    group: "Primitive",
    index,
    name: primitive,
    file: "normalized pose primitive",
    motion: displayMotionForPrimitive(primitive),
    primitive,
  }));
}

function displayMotionForPrimitive(
  primitive: (typeof motionPrimitiveNames)[number],
): Live2DMotionDebugInfo["motion"] {
  const entry = Object.entries(displayMotionPrimitiveMap).find(
    ([, candidate]) => candidate === primitive,
  );
  return entry?.[0] as Live2DMotionDebugInfo["motion"];
}

export function readLive2DIdList(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) {
    return value.map((item, index) => live2DIdToString(item, `#${index}`));
  }
  if (typeof value === "object" && "length" in value) {
    return Array.from(value as ArrayLike<unknown>, (item, index) =>
      live2DIdToString(item, `#${index}`),
    );
  }
  return [];
}

function getExpressionDebugInfo(
  settings: ModelSettingsDebugApi | null,
): Live2DExpressionDebugInfo[] {
  const definitions =
    settings?.expressions ?? settings?.json?.FileReferences?.Expressions ?? [];

  return definitions.map((definition, index) => {
    const file = live2DStringField(definition, ["File", "file"]) ?? "";
    const name =
      live2DStringField(definition, ["Name", "name", "Id", "id"]) ??
      file ??
      `Expression #${index}`;

    return {
      index,
      name,
      file,
    };
  });
}

function getNativeMotionDebugInfo(
  settings: ModelSettingsDebugApi | null,
): Live2DMotionDebugInfo[] {
  const groups =
    settings?.motions ?? settings?.json?.FileReferences?.Motions ?? {};

  return Object.entries(groups).flatMap(([group, definitions]) =>
    (definitions ?? []).map((definition, index) => ({
      source: "model" as const,
      group,
      index,
      name: `${group} #${index}`,
      file: live2DStringField(definition, ["File", "file"]) ?? "",
    })),
  );
}

function getKeyParameterDebugInfo(
  parameters: Live2DParameterDebugInfo[],
  manifest: Live2DModelManifest,
): Live2DKeyParameterDebugInfo[] {
  const lipSyncParameterIds = new Set(
    (manifest.lipSync.parameterIds.length
      ? manifest.lipSync.parameterIds
      : live2dParameterIdsByPoseChannel.mouthOpen
    ).map((id) => id.toLowerCase()),
  );

  return parameters.flatMap((parameter) => {
    const channels = parameterChannelsForId(parameter.id);
    const lipSync =
      manifest.lipSync.enabled &&
      lipSyncParameterIds.has(parameter.id.toLowerCase());
    if (!channels.length && !lipSync) return [];
    return [
      {
        ...parameter,
        channels,
        lipSync,
      },
    ];
  });
}

function parameterChannelsForId(id: string): NormalizedPoseChannel[] {
  const normalized = id.toLowerCase();
  return Object.entries(live2dParameterIdsByPoseChannel).flatMap(
    ([channel, ids]) =>
    ids.some((candidate) => candidate.toLowerCase() === normalized)
      ? [channel as NormalizedPoseChannel]
      : [],
  );
}

function getDrawableBounds(
  coreModel: CubismCoreModelDebugApi,
  index: number,
): Live2DDrawableDebugInfo["bounds"] {
  const vertices =
    coreModel.getDrawableVertexPositions?.(index) ??
    coreModel.getDrawableVertices?.(index);
  if (!vertices || vertices.length < 2) return null;

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;

  for (let cursor = 0; cursor < vertices.length - 1; cursor += 2) {
    const x = vertices[cursor];
    const y = vertices[cursor + 1];
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }

  if (![minX, minY, maxX, maxY].every(Number.isFinite)) return null;

  const width = maxX - minX;
  const height = maxY - minY;
  return {
    x: minX,
    y: minY,
    width,
    height,
    area: Math.max(width, 0) * Math.max(height, 0),
  };
}

function live2DIdAt(ids: string[], index: number, fallback: string): string {
  return ids[index] && ids[index] !== `#${index}` ? ids[index] : fallback;
}

function live2DIdToString(value: unknown, fallback: string): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return fallback;

  const candidate = value as {
    getString?: () => string;
    toString?: () => string;
    _id?: unknown;
    id?: unknown;
    s?: unknown;
  };
  if (typeof candidate.getString === "function") {
    return candidate.getString();
  }
  for (const key of ["_id", "id", "s"] as const) {
    const raw = candidate[key];
    if (typeof raw === "string") return raw;
    if (raw && typeof raw === "object" && "s" in raw) {
      const nested = (raw as { s?: unknown }).s;
      if (typeof nested === "string") return nested;
    }
  }

  const stringified = candidate.toString?.();
  return stringified && stringified !== "[object Object]"
    ? stringified
    : fallback;
}

function live2DStringField(value: unknown, keys: string[]): string | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  for (const key of keys) {
    const field = record[key];
    if (typeof field === "string" && field.length > 0) return field;
  }
  return null;
}
