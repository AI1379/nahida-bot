import { describe, expect, it } from "vitest";

import { importMotion3AsPortableAsset } from "./motion3PortableImporter";

const sourceParameters = [
  {
    id: "ParamAngleX",
    minimum: -30,
    maximum: 30,
    defaultValue: 0,
  },
  {
    id: "ParamMouthOpenY",
    minimum: 0,
    maximum: 1,
    defaultValue: 0,
  },
  {
    id: "ParamCustom",
    minimum: -10,
    maximum: 10,
    defaultValue: 0,
  },
] as const;

function motion3(curves: unknown[], metadata: Record<string, unknown> = {}) {
  return {
    Version: 3,
    Meta: {
      Duration: 1,
      Fps: 30,
      Loop: false,
      ...metadata,
    },
    Curves: curves,
  };
}

describe("importMotion3AsPortableAsset", () => {
  it("samples standard parameter curves into normalized portable frames", () => {
    const result = importMotion3AsPortableAsset(
      motion3([
        {
          Target: "Parameter",
          Id: "ParamAngleX",
          Segments: [0, 0, 0, 1, 30],
        },
        {
          Target: "Parameter",
          Id: "ParamMouthOpenY",
          Segments: [0, 0, 2, 1, 1],
        },
        {
          Target: "Parameter",
          Id: "ParamCustom",
          Segments: [0, 0, 0, 1, 10],
        },
        {
          Target: "PartOpacity",
          Id: "PartArm",
          Segments: [0, 1, 0, 1, 0],
        },
      ]),
      {
        assetId: "greet-1",
        name: "Greeting",
        sourceModelId: "source-model",
        sourceParameters: [...sourceParameters],
        sampleRateFps: 2,
      },
    );

    expect(result.asset).toMatchObject({
      schemaVersion: 1,
      id: "greet-1",
      name: "Greeting",
      durationMs: 1000,
      loopable: false,
      restoreAtEnd: true,
      channels: ["headYaw", "mouthOpen"],
      source: {
        format: "motion3",
        modelId: "source-model",
      },
    });
    expect(result.asset.frames).toHaveLength(3);
    expect(result.asset.frames[1]).toMatchObject({
      atMs: 500,
      headYaw: 0.5,
      mouthOpen: 0,
    });
    expect(result.asset.frames[2]).toMatchObject({
      atMs: 1000,
      headYaw: 1,
      mouthOpen: 1,
    });
    expect(result.report).toMatchObject({
      totalCurves: 4,
      parameterCurves: 3,
      importedCurves: 2,
      importedChannels: ["headYaw", "mouthOpen"],
    });
    expect(result.report.skippedCurves.map((curve) => curve.reason)).toEqual([
      "unmapped_parameter",
      "unsupported_target",
    ]);
  });

  it("preserves a symmetric Bezier curve when sampling", () => {
    const result = importMotion3AsPortableAsset(
      motion3([
        {
          Target: "Parameter",
          Id: "ParamAngleX",
          Segments: [0, 0, 1, 0.25, 0, 0.75, 30, 1, 30],
        },
      ]),
      {
        assetId: "bezier",
        sourceParameters: [...sourceParameters],
        sampleRateFps: 2,
      },
    );

    expect(result.asset.frames[1]?.headYaw).toBeCloseTo(0.5, 4);
  });

  it("supports explicit mappings for non-standard source parameters", () => {
    const result = importMotion3AsPortableAsset(
      motion3([
        {
          Target: "Parameter",
          Id: "ParamCustom",
          Segments: [0, 0, 0, 1, 10],
        },
      ]),
      {
        assetId: "custom-body",
        sourceParameters: [
          {
            ...sourceParameters[2],
            channel: "bodyYaw",
          },
        ],
        sampleRateFps: 1,
      },
    );

    expect(result.asset.channels).toEqual(["bodyYaw"]);
    expect(result.asset.frames.at(-1)?.bodyYaw).toBe(1);
  });

  it("rejects malformed and unsafe motion metadata", () => {
    expect(() =>
      importMotion3AsPortableAsset(
        motion3([
          {
            Target: "Parameter",
            Id: "ParamAngleX",
            Segments: [0, 0, 1, 0.5],
          },
        ]),
        {
          assetId: "broken",
          sourceParameters: [...sourceParameters],
        },
      ),
    ).toThrow("truncated");

    expect(() =>
      importMotion3AsPortableAsset(motion3([], { Duration: 301 }), {
        assetId: "too-long",
        sourceParameters: [],
      }),
    ).toThrow("duration");
  });
});
