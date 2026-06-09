import type { DisplayMotion } from "./displayPlan";

export type Live2DMotionSource = "model" | "procedural" | "none";

export interface Live2DModelMotionTarget {
  source: "model";
  group: string;
  index: number;
}

export interface Live2DProceduralMotionTarget {
  source: "procedural";
  motion: DisplayMotion;
}

export interface Live2DNoneMotionTarget {
  source: "none";
}

export type Live2DMotionTarget =
  | Live2DModelMotionTarget
  | Live2DProceduralMotionTarget
  | Live2DNoneMotionTarget;

export interface Live2DMotionOption {
  source: Exclude<Live2DMotionSource, "none">;
  group: string;
  index: number;
  name: string;
  file: string;
  motion?: DisplayMotion;
}

export interface Live2DExpressionOption {
  index: number;
  name: string;
  file: string;
}

export type Live2DExpressionMap = Record<string, string[]>;

export interface Live2DModelManifest {
  id: string;
  name: string;
  entry: string;
  source: "bundled" | "user_import";
  emotionMap: Live2DExpressionMap;
  motionMap: Partial<Record<DisplayMotion, Live2DMotionTarget>>;
  lipSync: {
    enabled: boolean;
    parameterIds: string[];
  };
}

const configuredModelEntry = import.meta.env.VITE_LIVE2D_MODEL_URL as
  | string
  | undefined;
const nahidaModelEntry = "/live2d_model/Nahida/Nahida.model3.json";
const nahida1080ModelEntry =
  "/live2d_model/Nahida_1080/Nahida_1080.model3.json";

const nahidaModelManifest: Live2DModelManifest = {
  id: "nahida",
  name: "Nahida",
  entry: nahidaModelEntry,
  source: "bundled",
  emotionMap: {
    happy: ["lh.exp3.json", "xx.exp3.json", "guang.exp3.json"],
    thinking: ["yz.exp3.json"],
    worried: ["lei.exp3.json"],
    error: ["sq.exp3.json"],
    offline: ["hei.exp3.json"],
  },
  motionMap: {
    idle: { source: "model", group: "Idle", index: 0 },
    nod: { source: "model", group: "Gesture", index: 0 },
    point: { source: "model", group: "Gesture", index: 0 },
    wave: { source: "model", group: "Gesture", index: 0 },
    notify: { source: "model", group: "Gesture", index: 0 },
    speaking: { source: "model", group: "Gesture", index: 0 },
  },
  lipSync: {
    enabled: true,
    parameterIds: ["ParamMouthOpenY", "ParamMouthForm"],
  },
};

const nahida1080ModelManifest: Live2DModelManifest = {
  id: "nahida-1080",
  name: "Nahida 1080",
  entry: nahida1080ModelEntry,
  source: "bundled",
  emotionMap: {
    neutral: ["shy_normal"],
    happy: ["Happy1", "StarEye"],
    thinking: ["Halfeyes"],
    worried: ["Sad1", "Sad2"],
    error: ["Angry"],
    offline: ["black"],
    hand: ["HandChange"],
    kusa: ["kusa"],
    shy: ["Shy"],
    star: ["StarEye"],
    wink: ["Wink"],
  },
  motionMap: {
    idle: { source: "procedural", motion: "idle" },
    nod: { source: "procedural", motion: "nod" },
    point: { source: "procedural", motion: "point" },
    wave: { source: "procedural", motion: "wave" },
    notify: { source: "procedural", motion: "notify" },
    speaking: { source: "procedural", motion: "speaking" },
  },
  lipSync: {
    enabled: true,
    parameterIds: ["ParamMouthOpenY"],
  },
};

const genericModelManifest: Live2DModelManifest = {
  id: "configured-live2d-model",
  name: "Configured Live2D Model",
  entry: configuredModelEntry ?? nahidaModelEntry,
  source: "user_import",
  emotionMap: {},
  motionMap: {},
  lipSync: {
    enabled: true,
    parameterIds: ["ParamMouthOpenY"],
  },
};

function isNahida1080Entry(entry: string): boolean {
  return entry.includes("/Nahida_1080/");
}

function isNahidaEntry(entry: string): boolean {
  return entry.includes("/Nahida/");
}

const configuredModelManifest =
  configuredModelEntry &&
  !isNahidaEntry(configuredModelEntry) &&
  !isNahida1080Entry(configuredModelEntry)
    ? [genericModelManifest]
    : [];

export const availableModelManifests: Live2DModelManifest[] = [
  ...configuredModelManifest,
  nahidaModelManifest,
  nahida1080ModelManifest,
];

export const mockModelManifest: Live2DModelManifest = configuredModelEntry
  ? isNahida1080Entry(configuredModelEntry)
    ? nahida1080ModelManifest
    : isNahidaEntry(configuredModelEntry)
      ? nahidaModelManifest
      : genericModelManifest
  : nahidaModelManifest;
