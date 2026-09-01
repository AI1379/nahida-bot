import { describe, expect, it } from "vitest";

import type { Live2DModelManifest } from "@/domain/live2d";
import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import type { Live2DDebugSnapshot } from "@/renderers/live2dRenderer";

import {
  createPortableMotionTargetModel,
  importPortableMotionForWorkbench,
  portableMotionSparklinePoints,
} from "./portableMotionWorkbench";

const manifest: Live2DModelManifest = {
  id: "target",
  name: "Target Model",
  entry: "/target.model3.json",
  source: "user_import",
  emotionMap: {},
  motionMap: {},
  lipSync: { enabled: true, parameterIds: ["ParamMouthOpenY"] },
  layout: { scale: 1, offsetX: 0, offsetY: 0, edgeExposedPx: 16 },
  performanceProfile: createDefaultModelPerformanceProfile("target"),
};

const snapshot = {
  modelName: "Target Model",
  expressions: [],
  nativeMotions: [],
  proceduralMotions: [],
  motions: [],
  keyParameters: [],
  parts: [],
  drawables: [],
  parameters: [
    {
      index: 0,
      id: "ParamAngleX",
      value: 0,
      minimum: -30,
      maximum: 30,
      defaultValue: 0,
      overridden: false,
      runtimeOverridden: false,
    },
    {
      index: 1,
      id: "ParamEyeLOpen",
      value: 1,
      minimum: 0,
      maximum: 1,
      defaultValue: 1,
      overridden: false,
      runtimeOverridden: false,
    },
  ],
} satisfies Live2DDebugSnapshot;

describe("portable motion Workbench", () => {
  it("captures exact active-model parameter ranges", () => {
    const target = createPortableMotionTargetModel(manifest, snapshot);

    expect(target).toMatchObject({
      modelId: "target",
      modelName: "Target Model",
      parameterIds: ["ParamAngleX", "ParamEyeLOpen"],
      parameters: [
        {
          id: "ParamAngleX",
          minimum: -30,
          maximum: 30,
          defaultValue: 0,
          channel: "headYaw",
        },
        {
          id: "ParamEyeLOpen",
          minimum: 0,
          maximum: 1,
          defaultValue: 1,
          channel: "eyeOpenLeft",
        },
      ],
    });
  });

  it("imports mtn data and exposes compatibility-safe curve summaries", () => {
    const target = createPortableMotionTargetModel(manifest, snapshot);
    const result = importPortableMotionForWorkbench(
      {
        fileName: "nod01.mtn",
        text: [
          "$fps=30",
          "PARAM_ANGLE_X=0,15,30",
          "PARAM_EYE_L_OPEN=1,0.5,0",
          "PARAM_BODY_ANGLE_X=0,1,2",
          "PARAM_PRIVATE=0,1,0",
        ].join("\n"),
      },
      target,
    );

    expect(result).toMatchObject({
      compatibilityStatus: "partial",
      poseCoverage: 2 / 3,
      supportedChannels: ["headYaw", "eyeOpenLeft"],
      missingChannels: ["bodyYaw"],
      audit: {
        format: "mtn",
        sourceItemCount: 4,
        importedItemCount: 3,
        skipped: [{ id: "PARAM_PRIVATE", reason: "unknown_parameter" }],
      },
    });
    expect(result.clip?.channels).toEqual(["headYaw", "eyeOpenLeft"]);
    expect(result.curves.map((curve) => curve.targetParameterId)).toEqual([
      "ParamAngleX",
      null,
      "ParamEyeLOpen",
    ]);
  });

  it("imports target-specific motion3 JSON", () => {
    const target = createPortableMotionTargetModel(manifest, snapshot);
    const result = importPortableMotionForWorkbench(
      {
        fileName: "converted.motion3.json",
        text: JSON.stringify({
          Version: 3,
          Meta: { Duration: 1, Fps: 30, Loop: false },
          Curves: [
            {
              Target: "Parameter",
              Id: "ParamAngleX",
              Segments: [0, 0, 0, 1, 30],
            },
          ],
        }),
      },
      target,
    );

    expect(result.audit).toMatchObject({
      format: "motion3",
      sourceItemCount: 1,
      importedItemCount: 1,
    });
    expect(result.compatibilityStatus).toBe("full");
    expect(result.curves[0]?.end).toBe(1);
  });

  it("rejects unsupported files and oversized input before parsing", () => {
    const target = createPortableMotionTargetModel(manifest, snapshot);
    expect(() =>
      importPortableMotionForWorkbench(
        { fileName: "motion.txt", text: "$fps=30" },
        target,
      ),
    ).toThrow(".mtn");
    expect(() =>
      importPortableMotionForWorkbench(
        { fileName: "huge.mtn", text: "", size: 17 * 1024 * 1024 },
        target,
      ),
    ).toThrow("16 MiB");
  });

  it("creates stable sparkline coordinates for flat and changing samples", () => {
    expect(portableMotionSparklinePoints([0, 0])).toBe("0.00,21.00 180.00,21.00");
    expect(portableMotionSparklinePoints([-1, 0, 1], 100, 20)).toBe(
      "0.00,20.00 50.00,10.00 100.00,0.00",
    );
  });

  it("bounds curve preview points without reducing playback frames", () => {
    const target = createPortableMotionTargetModel(manifest, snapshot);
    const samples = Array.from({ length: 300 }, (_, index) => String(index / 10));
    const result = importPortableMotionForWorkbench(
      {
        fileName: "long.mtn",
        text: `$fps=30\nPARAM_ANGLE_X=${samples.join(",")}`,
      },
      target,
    );

    expect(result.asset.frames).toHaveLength(300);
    expect(result.curves[0]?.samples).toHaveLength(240);
    expect(result.curves[0]?.samples.at(-1)).toBeCloseTo(29.9 / 30);
  });
});
