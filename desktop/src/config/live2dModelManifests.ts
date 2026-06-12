import { desktopWindowDefaults } from "@/config/desktopRuntimeDefaults";
import type { Live2DModelManifest } from "@/domain/live2d";

const configuredModelEntry = import.meta.env.VITE_LIVE2D_MODEL_URL as
  | string
  | undefined;

const defaultLayout = {
  scale: 1,
  offsetX: 0,
  offsetY: 0,
  edgeExposedPx: desktopWindowDefaults.exposedPx,
} as const;

const nahidaModelManifest: Live2DModelManifest = {
  id: "nahida",
  name: "Nahida",
  entry: "/live2d_model/Nahida/Nahida.model3.json",
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
    emerge: { source: "procedural", motion: "emerge" },
    retreat: { source: "procedural", motion: "retreat" },
  },
  lipSync: {
    enabled: true,
    parameterIds: ["ParamMouthOpenY", "ParamMouthForm"],
  },
  layout: { ...defaultLayout },
};

const nahida1080ModelManifest: Live2DModelManifest = {
  id: "nahida-1080",
  name: "Nahida 1080",
  entry: "/live2d_model/Nahida_1080/Nahida_1080.model3.json",
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
    emerge: { source: "procedural", motion: "emerge" },
    retreat: { source: "procedural", motion: "retreat" },
  },
  lipSync: {
    enabled: true,
    parameterIds: ["ParamMouthOpenY"],
  },
  layout: { ...defaultLayout },
};

const genericModelManifest: Live2DModelManifest = {
  id: "configured-live2d-model",
  name: "Configured Live2D Model",
  entry: configuredModelEntry ?? nahidaModelManifest.entry,
  source: "user_import",
  emotionMap: {},
  motionMap: {},
  lipSync: {
    enabled: true,
    parameterIds: ["ParamMouthOpenY"],
  },
  layout: { ...defaultLayout },
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

export const defaultModelManifest: Live2DModelManifest = configuredModelEntry
  ? isNahida1080Entry(configuredModelEntry)
    ? nahida1080ModelManifest
    : isNahidaEntry(configuredModelEntry)
      ? nahidaModelManifest
      : genericModelManifest
  : nahida1080ModelManifest;
