import type { DisplayEmotion, DisplayMotion } from "./displayPlan";

export interface Live2DMotionTarget {
  group: string;
  index: number;
}

export interface Live2DModelManifest {
  id: string;
  name: string;
  entry: string;
  source: "bundled" | "user_import";
  emotionMap: Partial<Record<DisplayEmotion, string[]>>;
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
    idle: { group: "Idle", index: 0 },
    nod: { group: "Gesture", index: 0 },
    point: { group: "Gesture", index: 0 },
    wave: { group: "Gesture", index: 0 },
    notify: { group: "Gesture", index: 0 },
    speaking: { group: "Gesture", index: 0 },
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
  },
  motionMap: {
    idle: { group: "Idle", index: 0 },
    nod: { group: "Gesture", index: 0 },
    point: { group: "Gesture", index: 1 },
    wave: { group: "Gesture", index: 2 },
    notify: { group: "Notification", index: 0 },
    speaking: { group: "Talk", index: 0 },
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
