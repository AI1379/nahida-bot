import { describe, expect, it } from "vitest";

import type { BaseMotionProfile } from "@/domain/live2dBaseMotion";

import {
  createRuntimeParameterOverride,
  groupProceduralTargets,
  runtimeParameterValueAt,
} from "./live2dProceduralMotion";

const profile = {
  durationMs: 1000,
  keyframes: [
    {
      atMs: 500,
      targets: [
        { role: "headX", value: 20 },
        { role: "mouthOpen", value: 0.75 },
      ],
    },
  ],
} satisfies BaseMotionProfile;

describe("live2dProceduralMotion", () => {
  it("groups profile targets by common parameter role", () => {
    const grouped = groupProceduralTargets(profile);

    expect(grouped.get("headX")).toEqual([{ atMs: 500, value: 20 }]);
    expect(grouped.get("mouthOpen")).toEqual([{ atMs: 500, value: 0.75 }]);
  });

  it("creates clamped runtime overrides and interpolates them", () => {
    const override = createRuntimeParameterOverride({
      profile,
      current: 0,
      minimum: -10,
      maximum: 10,
      targets: [{ atMs: 500, value: 20 }],
      startedAt: 123,
    });

    expect(override).toMatchObject({
      original: 0,
      startedAt: 123,
      durationMs: 1000,
      keyframes: [
        { atMs: 0, value: 0 },
        { atMs: 500, value: 10 },
        { atMs: 1000, value: 0 },
      ],
    });
    expect(runtimeParameterValueAt(override, 250)).toBeCloseTo(5);
    expect(runtimeParameterValueAt(override, 500)).toBeCloseTo(10);
    expect(runtimeParameterValueAt(override, 1000)).toBeCloseTo(0);
  });
});
