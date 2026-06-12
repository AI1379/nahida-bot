import { describe, expect, it } from "vitest";

import {
  createDefaultLocalDesktopConfig,
  configuredModelFromManifest,
  modelMappingConfigFromManifest,
} from "./config";
import type { Live2DModelManifest } from "./live2d";

const manifest: Live2DModelManifest = {
  id: "test-model",
  name: "Test Model",
  entry: "/test.model3.json",
  source: "bundled",
  emotionMap: { happy: ["happy-a", "happy-b"] },
  motionMap: { nod: { source: "procedural", motion: "nod" } },
  lipSync: { enabled: true, parameterIds: ["ParamMouthOpenY"] },
  layout: {
    scale: 1.1,
    offsetX: 12,
    offsetY: -8,
    edgeExposedPx: 36,
  },
};

describe("configuredModelFromManifest", () => {
  it("defaults system TTS to Chinese automatic voice selection", () => {
    expect(createDefaultLocalDesktopConfig(manifest).ttsSettings).toEqual({
      language: "zh-CN",
      voiceUri: "",
      preferFemale: true,
      rate: 1,
      pitch: 0,
      volume: 1,
    });
  });

  it("uses the local model config as the runtime source of truth", () => {
    const config = modelMappingConfigFromManifest(manifest);
    config.expressionMap = { happy: ["custom-happy"] };
    config.lipSync = { enabled: false, parameterIds: ["CustomMouth"] };
    config.scale = 1.4;
    config.offsetX = 24;

    const configured = configuredModelFromManifest(manifest, config);

    expect(configured.emotionMap).toEqual({ happy: ["custom-happy"] });
    expect(configured.lipSync).toEqual({
      enabled: false,
      parameterIds: ["CustomMouth"],
    });
    expect(configured.layout).toEqual({
      scale: 1.4,
      offsetX: 24,
      offsetY: -8,
      edgeExposedPx: 36,
    });
  });
});
