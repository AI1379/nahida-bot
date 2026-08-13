import type { DisplayMotion } from "./displayPlan";
import {
  createNormalizedPoseFrame,
  neutralNormalizedPose,
  normalizedPoseChannelRanges,
  type NormalizedMotionClip,
  type NormalizedPoseChannel,
  type NormalizedPoseValues,
} from "./normalizedPose";

export const motionPrimitiveNames = [
  "idle-breathe",
  "blink",
  "nod",
  "point",
  "wave",
  "notify",
  "speaking",
  "emerge",
  "retreat",
  "glance",
  "glance-right",
  "shake",
  "think-loop",
  "explain-small",
  "surprised-pop",
  "sad-drop",
  "happy-bounce",
  "celebrate",
] as const;

export type MotionPrimitiveName = (typeof motionPrimitiveNames)[number];

interface MotionPrimitiveKeyframe {
  /** Position inside one cycle, in the inclusive range 0..1. */
  at: number;
  /** Normalized offsets from startPose. */
  values: Partial<NormalizedPoseValues>;
}

interface MotionPrimitiveDefinition {
  defaultDurationMs: number;
  referenceIntensity: number;
  loopable: boolean;
  channels: NormalizedPoseChannel[];
  keyframes: MotionPrimitiveKeyframe[];
}

export interface GenerateMotionPrimitiveOptions {
  clipId: string;
  intentId: string;
  durationMs?: number;
  intensity?: number;
  repeat?: number;
  startPose?: Partial<NormalizedPoseValues>;
  loopable?: boolean;
}

const motionPrimitiveDefinitions: Record<
  MotionPrimitiveName,
  MotionPrimitiveDefinition
> = {
  "idle-breathe": {
    defaultDurationMs: 2400,
    referenceIntensity: 0.2,
    loopable: true,
    channels: ["headPitch", "bodyPitch", "breath"],
    keyframes: [
      { at: 0.5, values: { headPitch: 0.03, bodyPitch: 0.05, breath: 1 } },
    ],
  },
  blink: {
    defaultDurationMs: 320,
    referenceIntensity: 0.5,
    loopable: false,
    channels: ["eyeOpenLeft", "eyeOpenRight"],
    keyframes: [
      { at: 0.3, values: { eyeOpenLeft: -0.95, eyeOpenRight: -0.95 } },
      { at: 0.62, values: { eyeOpenLeft: 0, eyeOpenRight: 0 } },
    ],
  },
  nod: {
    defaultDurationMs: 1180,
    referenceIntensity: 0.45,
    loopable: false,
    channels: ["headPitch", "bodyPitch"],
    keyframes: [
      { at: 0.2, values: { headPitch: 0.4, bodyPitch: 0.12 } },
      { at: 0.44, values: { headPitch: -0.17, bodyPitch: -0.05 } },
      { at: 0.66, values: { headPitch: 0.18, bodyPitch: 0.055 } },
    ],
  },
  point: {
    defaultDurationMs: 1320,
    referenceIntensity: 0.5,
    loopable: false,
    channels: ["headYaw", "bodyYaw", "gazeX", "browUpLeft", "browUpRight"],
    keyframes: [
      { at: 0.2, values: { headYaw: -0.27, bodyYaw: -0.12, gazeX: -0.18 } },
      {
        at: 0.53,
        values: {
          headYaw: -0.33,
          bodyYaw: -0.18,
          gazeX: -0.28,
          browUpLeft: 0.25,
          browUpRight: 0.25,
        },
      },
    ],
  },
  wave: {
    defaultDurationMs: 1440,
    referenceIntensity: 0.6,
    loopable: false,
    channels: ["headRoll", "bodyRoll", "gazeX"],
    keyframes: [
      { at: 0.17, values: { headRoll: 0.23, bodyRoll: 0.12, gazeX: 0.12 } },
      { at: 0.39, values: { headRoll: -0.2, bodyRoll: -0.09, gazeX: -0.1 } },
      { at: 0.61, values: { headRoll: 0.2, bodyRoll: 0.09, gazeX: 0.1 } },
    ],
  },
  notify: {
    defaultDurationMs: 1120,
    referenceIntensity: 0.65,
    loopable: false,
    channels: [
      "headPitch",
      "gazeY",
      "browUpLeft",
      "browUpRight",
      "mouthOpen",
    ],
    keyframes: [
      {
        at: 0.2,
        values: {
          headPitch: -0.27,
          gazeY: 0.14,
          browUpLeft: 0.35,
          browUpRight: 0.35,
          mouthOpen: 0.22,
        },
      },
      {
        at: 0.52,
        values: {
          headPitch: -0.13,
          gazeY: 0.08,
          browUpLeft: 0.2,
          browUpRight: 0.2,
          mouthOpen: 0.12,
        },
      },
    ],
  },
  speaking: {
    defaultDurationMs: 1040,
    referenceIntensity: 0.35,
    loopable: true,
    channels: ["headPitch", "bodyPitch", "mouthSmile"],
    keyframes: [
      { at: 0.23, values: { headPitch: -0.087, bodyPitch: -0.025, mouthSmile: 0.15 } },
      { at: 0.54, values: { headPitch: 0.06, bodyPitch: 0.018, mouthSmile: 0.05 } },
    ],
  },
  emerge: {
    defaultDurationMs: 760,
    referenceIntensity: 0.65,
    loopable: false,
    channels: ["headRoll", "headPitch", "bodyRoll", "gazeX", "browUpLeft", "browUpRight"],
    keyframes: [
      {
        at: 0.18,
        values: {
          headRoll: -0.2,
          headPitch: 0.2,
          bodyRoll: -0.14,
          gazeX: -0.2,
          browUpLeft: 0.2,
          browUpRight: 0.2,
        },
      },
      { at: 0.55, values: { headRoll: 0.1, headPitch: -0.067, bodyRoll: 0.07, gazeX: 0.08 } },
      { at: 0.84, values: { headRoll: -0.033, headPitch: 0.027, bodyRoll: -0.02 } },
    ],
  },
  retreat: {
    defaultDurationMs: 620,
    referenceIntensity: 0.55,
    loopable: false,
    channels: ["headRoll", "headPitch", "bodyRoll", "gazeX", "gazeY"],
    keyframes: [
      { at: 0.19, values: { headRoll: 0.13, headPitch: -0.17, bodyRoll: 0.1, gazeX: 0.22, gazeY: -0.1 } },
      { at: 0.74, values: { headRoll: 0.05, headPitch: -0.05, bodyRoll: 0.03, gazeX: 0.1 } },
    ],
  },
  glance: {
    defaultDurationMs: 900,
    referenceIntensity: 0.35,
    loopable: false,
    channels: ["headYaw", "gazeX", "gazeY"],
    keyframes: [
      { at: 0.25, values: { headYaw: -0.08, gazeX: -0.45, gazeY: -0.08 } },
      { at: 0.65, values: { headYaw: -0.04, gazeX: -0.35, gazeY: -0.04 } },
    ],
  },
  "glance-right": {
    defaultDurationMs: 900,
    referenceIntensity: 0.35,
    loopable: false,
    channels: ["headYaw", "gazeX", "gazeY"],
    keyframes: [
      { at: 0.25, values: { headYaw: 0.08, gazeX: 0.45, gazeY: -0.08 } },
      { at: 0.65, values: { headYaw: 0.04, gazeX: 0.35, gazeY: -0.04 } },
    ],
  },
  shake: {
    defaultDurationMs: 920,
    referenceIntensity: 0.5,
    loopable: false,
    channels: ["headYaw", "bodyYaw", "gazeX"],
    keyframes: [
      { at: 0.2, values: { headYaw: -0.28, bodyYaw: -0.06, gazeX: -0.12 } },
      { at: 0.42, values: { headYaw: 0.28, bodyYaw: 0.06, gazeX: 0.12 } },
      { at: 0.64, values: { headYaw: -0.18, bodyYaw: -0.04, gazeX: -0.08 } },
      { at: 0.82, values: { headYaw: 0.1, bodyYaw: 0.02, gazeX: 0.04 } },
    ],
  },
  "think-loop": {
    defaultDurationMs: 1800,
    referenceIntensity: 0.32,
    loopable: true,
    channels: ["headYaw", "headPitch", "gazeX", "gazeY", "browUpLeft", "browUpRight"],
    keyframes: [
      { at: 0.2, values: { headYaw: -0.08, headPitch: 0.08, gazeX: -0.28, gazeY: -0.18 } },
      { at: 0.52, values: { headYaw: 0.03, headPitch: 0.04, gazeX: -0.12, gazeY: -0.1, browUpLeft: 0.08, browUpRight: 0.08 } },
      { at: 0.78, values: { headYaw: -0.04, headPitch: 0.07, gazeX: -0.22, gazeY: -0.15 } },
    ],
  },
  "explain-small": {
    defaultDurationMs: 1300,
    referenceIntensity: 0.38,
    loopable: false,
    channels: ["headYaw", "headPitch", "bodyYaw", "bodyPitch", "gazeX", "mouthSmile"],
    keyframes: [
      { at: 0.2, values: { headYaw: -0.08, headPitch: -0.06, bodyYaw: -0.04, gazeX: -0.06, mouthSmile: 0.12 } },
      { at: 0.48, values: { headYaw: 0.08, headPitch: 0.03, bodyYaw: 0.04, bodyPitch: 0.03, gazeX: 0.06, mouthSmile: 0.08 } },
      { at: 0.75, values: { headYaw: -0.03, headPitch: -0.03, bodyYaw: -0.02, gazeX: -0.02, mouthSmile: 0.1 } },
    ],
  },
  "surprised-pop": {
    defaultDurationMs: 760,
    referenceIntensity: 0.65,
    loopable: false,
    channels: ["headPitch", "bodyPitch", "browUpLeft", "browUpRight", "mouthOpen"],
    keyframes: [
      {
        at: 0.18,
        values: {
          headPitch: -0.22,
          bodyPitch: -0.12,
          browUpLeft: 0.5,
          browUpRight: 0.5,
          mouthOpen: 0.28,
        },
      },
      { at: 0.58, values: { headPitch: -0.08, bodyPitch: -0.04, browUpLeft: 0.2, browUpRight: 0.2, mouthOpen: 0.1 } },
    ],
  },
  "sad-drop": {
    defaultDurationMs: 1250,
    referenceIntensity: 0.4,
    loopable: false,
    channels: ["headPitch", "bodyPitch", "gazeY", "browUpLeft", "browUpRight", "mouthSmile"],
    keyframes: [
      { at: 0.28, values: { headPitch: 0.2, bodyPitch: 0.12, gazeY: -0.2, browUpLeft: -0.16, browUpRight: -0.16, mouthSmile: -0.18 } },
      { at: 0.68, values: { headPitch: 0.14, bodyPitch: 0.08, gazeY: -0.14, browUpLeft: -0.1, browUpRight: -0.1, mouthSmile: -0.12 } },
    ],
  },
  "happy-bounce": {
    defaultDurationMs: 1100,
    referenceIntensity: 0.6,
    loopable: false,
    channels: ["headPitch", "headRoll", "bodyPitch", "bodyRoll", "mouthSmile"],
    keyframes: [
      { at: 0.2, values: { headPitch: 0.12, headRoll: -0.08, bodyPitch: -0.12, bodyRoll: -0.07, mouthSmile: 0.4 } },
      { at: 0.45, values: { headPitch: -0.05, headRoll: 0.08, bodyPitch: 0.08, bodyRoll: 0.07, mouthSmile: 0.3 } },
      { at: 0.7, values: { headPitch: 0.08, headRoll: -0.04, bodyPitch: -0.06, bodyRoll: -0.03, mouthSmile: 0.35 } },
    ],
  },
  celebrate: {
    defaultDurationMs: 1650,
    referenceIntensity: 0.68,
    loopable: false,
    channels: ["headPitch", "headRoll", "bodyPitch", "bodyRoll", "browUpLeft", "browUpRight", "mouthSmile", "energy"],
    keyframes: [
      { at: 0.14, values: { headPitch: -0.14, headRoll: -0.1, bodyPitch: -0.15, bodyRoll: -0.08, browUpLeft: 0.3, browUpRight: 0.3, mouthSmile: 0.5, energy: 0.25 } },
      { at: 0.34, values: { headPitch: 0.12, headRoll: 0.1, bodyPitch: 0.1, bodyRoll: 0.08, browUpLeft: 0.22, browUpRight: 0.22, mouthSmile: 0.42, energy: 0.18 } },
      { at: 0.55, values: { headPitch: -0.12, headRoll: -0.08, bodyPitch: -0.12, bodyRoll: -0.06, browUpLeft: 0.28, browUpRight: 0.28, mouthSmile: 0.48, energy: 0.22 } },
      { at: 0.78, values: { headPitch: 0.05, headRoll: 0.04, bodyPitch: 0.04, bodyRoll: 0.03, mouthSmile: 0.3, energy: 0.12 } },
    ],
  },
};

export const displayMotionPrimitiveMap: Record<
  DisplayMotion,
  MotionPrimitiveName
> = {
  idle: "idle-breathe",
  nod: "nod",
  point: "point",
  wave: "wave",
  notify: "notify",
  speaking: "speaking",
  emerge: "emerge",
  retreat: "retreat",
};

export const motionPrimitiveBoundary =
  "Procedural primitives only drive canonical pose channels. They do not synthesize missing arms, meshes, textures, or authored poses.";

export function isMotionPrimitiveName(value: unknown): value is MotionPrimitiveName {
  return motionPrimitiveNames.includes(value as MotionPrimitiveName);
}

export function motionPrimitiveDefaultDurationMs(
  name: MotionPrimitiveName,
): number {
  return motionPrimitiveDefinitions[name].defaultDurationMs;
}

export function motionPrimitiveIsLoopable(name: MotionPrimitiveName): boolean {
  return motionPrimitiveDefinitions[name].loopable;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function finitePositive(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : fallback;
}

function normalizedRepeat(value: number | undefined): number {
  return typeof value === "number" && Number.isFinite(value)
    ? clamp(Math.round(value), 1, 8)
    : 1;
}

function poseWithOffsets(
  startPose: NormalizedPoseValues,
  offsets: Partial<NormalizedPoseValues>,
  scale: number,
  energy: number,
): NormalizedPoseValues {
  const pose: NormalizedPoseValues = { ...startPose, energy };
  for (const [channel, offset] of Object.entries(offsets) as Array<
    [NormalizedPoseChannel, number]
  >) {
    const range = normalizedPoseChannelRanges[channel];
    pose[channel] = clamp(
      startPose[channel] + offset * scale,
      range.minimum,
      range.maximum,
    );
  }
  return pose;
}

export function generateMotionPrimitive(
  name: MotionPrimitiveName,
  options: GenerateMotionPrimitiveOptions,
): NormalizedMotionClip {
  const definition = motionPrimitiveDefinitions[name];
  const durationMs = finitePositive(
    options.durationMs,
    definition.defaultDurationMs,
  );
  const intensity = clamp(
    typeof options.intensity === "number" && Number.isFinite(options.intensity)
      ? options.intensity
      : definition.referenceIntensity,
    0,
    1,
  );
  const repeat = normalizedRepeat(options.repeat);
  const cycleDurationMs = durationMs / repeat;
  const scale = intensity / Math.max(definition.referenceIntensity, 0.01);
  const startPose: NormalizedPoseValues = {
    ...neutralNormalizedPose,
    ...options.startPose,
  };
  const frames = [createNormalizedPoseFrame(0, startPose)];

  for (let cycle = 0; cycle < repeat; cycle += 1) {
    const cycleStartMs = cycle * cycleDurationMs;
    for (const keyframe of definition.keyframes) {
      frames.push(
        createNormalizedPoseFrame(
          cycleStartMs + keyframe.at * cycleDurationMs,
          poseWithOffsets(startPose, keyframe.values, scale, intensity),
        ),
      );
    }
    frames.push(createNormalizedPoseFrame(cycleStartMs + cycleDurationMs, startPose));
  }

  return {
    id: options.clipId,
    intentId: options.intentId,
    durationMs,
    loopable: options.loopable ?? definition.loopable,
    restoreAtEnd: true,
    channels: [...definition.channels],
    frames,
  };
}
