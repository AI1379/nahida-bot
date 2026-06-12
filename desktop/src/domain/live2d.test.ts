import { describe, expect, it } from "vitest";

import {
  defaultModelManifest,
} from "@/config/live2dModelManifests";
import { live2dModelLoadKey, type Live2DModelManifest } from "./live2d";

const model: Live2DModelManifest = {
  id: "nahida-1080",
  name: "Nahida 1080",
  entry: "/live2d_model/Nahida_1080/Nahida_1080.model3.json",
  source: "bundled",
  emotionMap: { happy: ["Happy1"] },
  motionMap: {},
  lipSync: { enabled: true, parameterIds: ["ParamMouthOpenY"] },
  layout: {
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    edgeExposedPx: 42,
  },
};

describe("Live2D model loading", () => {
  it("uses Nahida 1080 as the built-in default", () => {
    expect(defaultModelManifest.id).toBe("nahida-1080");
  });

  it("does not reload for presentation-only manifest changes", () => {
    const changedPresentation = {
      ...model,
      emotionMap: { happy: ["StarEye"] },
      layout: { ...model.layout, scale: 1.2 },
    };

    expect(live2dModelLoadKey(changedPresentation)).toBe(
      live2dModelLoadKey(model),
    );
  });

  it("reloads when the model asset entry changes", () => {
    expect(
      live2dModelLoadKey({ ...model, entry: "/replacement.model3.json" }),
    ).not.toBe(live2dModelLoadKey(model));
  });
});
