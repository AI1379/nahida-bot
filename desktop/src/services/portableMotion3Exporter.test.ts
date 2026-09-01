import { describe, expect, it } from "vitest";

import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";

import { cubism3StandardTargetParameters } from "./cubismStandardSourceParameters";
import { importMotion3AsPortableAsset } from "./motion3PortableImporter";
import { importMtnAsPortableAsset } from "./mtnPortableImporter";
import { exportPortableMotionAsMotion3 } from "./portableMotion3Exporter";

function portableSample() {
  return importMtnAsPortableAsset(
    "$fps=30\nPARAM_ANGLE_X=0,15,30\nPARAM_EYE_L_OPEN=1,0.5,0",
    { assetId: "nod" },
  ).asset;
}

describe("exportPortableMotionAsMotion3", () => {
  it("retargets portable frames to Cubism 3 standard parameters", () => {
    const profile = createDefaultModelPerformanceProfile("target");
    const result = exportPortableMotionAsMotion3(portableSample(), {
      targetParameters: cubism3StandardTargetParameters,
      poseParameterMap: profile.poseParameterMap,
    });

    expect(result.report).toEqual({
      exportedChannels: ["headYaw", "eyeOpenLeft"],
      missingChannels: [],
      omittedFeatureIds: [],
    });
    expect(result.document.Meta).toMatchObject({
      Duration: 2 / 30,
      Fps: 30,
      Loop: false,
      CurveCount: 2,
      TotalSegmentCount: 4,
      TotalPointCount: 6,
    });
    expect(result.document.Curves).toEqual([
      {
        Target: "Parameter",
        Id: "ParamAngleX",
        Segments: [0, 0, 0, 1 / 30, 15, 0, 2 / 30, 30],
      },
      {
        Target: "Parameter",
        Id: "ParamEyeLOpen",
        Segments: [0, 1, 0, 1 / 30, 0.5, 0, 2 / 30, 0],
      },
    ]);
  });

  it("reports target channels that cannot be emitted", () => {
    const profile = createDefaultModelPerformanceProfile("limited-target");
    const result = exportPortableMotionAsMotion3(portableSample(), {
      targetParameters: cubism3StandardTargetParameters.filter(
        (parameter) => parameter.id === "ParamAngleX",
      ),
      poseParameterMap: profile.poseParameterMap,
    });

    expect(result.report.exportedChannels).toEqual(["headYaw"]);
    expect(result.report.missingChannels).toEqual(["eyeOpenLeft"]);
  });

  it("converts global and per-channel fade timing to seconds", () => {
    const profile = createDefaultModelPerformanceProfile("target");
    const asset = importMtnAsPortableAsset(
      [
        "$fps=30",
        "$fadein=1000",
        "$fadeout=500",
        "$fadein:PARAM_ANGLE_X=250",
        "PARAM_ANGLE_X=0,30",
      ].join("\n"),
      { assetId: "faded" },
    ).asset;
    const result = exportPortableMotionAsMotion3(asset, {
      targetParameters: cubism3StandardTargetParameters,
      poseParameterMap: profile.poseParameterMap,
    });

    expect(result.document.Meta).toMatchObject({
      FadeInTime: 1,
      FadeOutTime: 0.5,
    });
    expect(result.document.Curves[0]).toMatchObject({
      Id: "ParamAngleX",
      FadeInTime: 0.25,
    });
  });

  it("round-trips normalized samples through the emitted motion3 document", () => {
    const profile = createDefaultModelPerformanceProfile("target");
    const exported = exportPortableMotionAsMotion3(portableSample(), {
      targetParameters: cubism3StandardTargetParameters,
      poseParameterMap: profile.poseParameterMap,
    });
    const imported = importMotion3AsPortableAsset(exported.document, {
      assetId: "round-trip",
      sourceParameters: cubism3StandardTargetParameters,
      sampleRateFps: 30,
    });

    expect(imported.asset.frames).toHaveLength(3);
    expect(imported.asset.frames[1]).toMatchObject({
      headYaw: 0.5,
      eyeOpenLeft: 0.5,
    });
    expect(imported.asset.frames.at(-1)).toMatchObject({
      headYaw: 1,
      eyeOpenLeft: 0,
    });
  });
});
