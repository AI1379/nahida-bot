import { describe, expect, it } from "vitest";

import { importMtnAsPortableAsset } from "./mtnPortableImporter";

const sampleMtn = `
# Live2D Animator Motion Data
$fps=30
$fadein=1000
$fadeout=500
$fadein:PARAM_ANGLE_X=250
PARAM_IMPORT=37
PARAM_ANGLE_X=0,15,30
PARAM_EYE_L_OPEN=1,0.5,0
PARAM_CUSTOM=0
`;

describe("importMtnAsPortableAsset", () => {
  it("imports sampled Cubism 2 channels and preserves timing metadata", () => {
    const result = importMtnAsPortableAsset(sampleMtn, {
      assetId: "nod",
      name: "Nod",
      sourceModelId: "source-model",
    });

    expect(result.asset).toMatchObject({
      schemaVersion: 1,
      id: "nod",
      name: "Nod",
      durationMs: 2000 / 30,
      fadeInMs: 1000,
      fadeOutMs: 500,
      loopable: false,
      restoreAtEnd: true,
      channels: ["headYaw", "eyeOpenLeft"],
      source: {
        format: "mtn",
        modelId: "source-model",
      },
      channelFades: [{ channel: "headYaw", fadeInMs: 250 }],
    });
    expect(result.asset.frames).toEqual([
      { atMs: 0, headYaw: 0, eyeOpenLeft: 1 },
      { atMs: 1000 / 30, headYaw: 0.5, eyeOpenLeft: 0.5 },
      { atMs: 2000 / 30, headYaw: 1, eyeOpenLeft: 0 },
    ]);
    expect(result.report).toMatchObject({
      fps: 30,
      frameCount: 3,
      fadeInMs: 1000,
      fadeOutMs: 500,
      parameterImportHint: 37,
      totalParameters: 3,
      importedParameters: 2,
      importedChannels: ["headYaw", "eyeOpenLeft"],
      assumedRangeParameterIds: ["PARAM_ANGLE_X", "PARAM_EYE_L_OPEN"],
      parameterFades: [{ id: "PARAM_ANGLE_X", fadeInMs: 250 }],
      clampedParameters: [],
      skippedParameters: [{ id: "PARAM_CUSTOM", reason: "unknown_parameter" }],
    });
  });

  it("reports standard-range clipping and accepts a model-specific override", () => {
    const text = "$fps=30\nPARAM_EYE_L_OPEN=1.5,1";
    const assumed = importMtnAsPortableAsset(text, { assetId: "assumed" });
    const calibrated = importMtnAsPortableAsset(text, {
      assetId: "calibrated",
      sourceParameters: [
        {
          id: "PARAM_EYE_L_OPEN",
          minimum: 0,
          maximum: 1.5,
          defaultValue: 1.5,
        },
      ],
    });

    expect(assumed.report.clampedParameters).toEqual([
      { id: "PARAM_EYE_L_OPEN", sampleCount: 1 },
    ]);
    expect(assumed.asset.frames[0]?.eyeOpenLeft).toBe(1);
    expect(calibrated.report.clampedParameters).toEqual([]);
    expect(calibrated.report.assumedRangeParameterIds).toEqual([]);
    expect(calibrated.asset.frames[1]?.eyeOpenLeft).toBeCloseTo(2 / 3);
  });

  it("retains unknown directives in the compatibility report", () => {
    const result = importMtnAsPortableAsset(
      "$fps=30\n$unknown=value\nPARAM_ANGLE_X=0",
      { assetId: "unknown-directive" },
    );

    expect(result.report.unsupportedDirectives).toEqual(["$unknown=value"]);
  });

  it("rejects inconsistent parameter sample lengths", () => {
    expect(() =>
      importMtnAsPortableAsset(
        "$fps=30\nPARAM_ANGLE_X=0,1,2\nPARAM_ANGLE_Y=0,1",
        { assetId: "broken" },
      ),
    ).toThrow("expected 1 or 3");
  });
});
