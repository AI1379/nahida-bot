import type { DisplayMotion } from "./displayPlan";

export type CommonLive2DParameterRole =
  | "headX"
  | "headY"
  | "headZ"
  | "bodyX"
  | "bodyY"
  | "bodyZ"
  | "eyeX"
  | "eyeY"
  | "browY"
  | "mouthOpen"
  | "mouthForm";

export interface BaseMotionTarget {
  role: CommonLive2DParameterRole;
  value: number;
}

export interface BaseMotionKeyframe {
  atMs: number;
  targets: BaseMotionTarget[];
}

export interface BaseMotionProfile {
  durationMs: number;
  keyframes: BaseMotionKeyframe[];
}

export const commonLive2DParameterIds: Record<
  CommonLive2DParameterRole,
  string[]
> = {
  headX: ["ParamAngleX", "PARAM_ANGLE_X"],
  headY: ["ParamAngleY", "PARAM_ANGLE_Y"],
  headZ: ["ParamAngleZ", "PARAM_ANGLE_Z"],
  bodyX: ["ParamBodyAngleX", "PARAM_BODY_ANGLE_X"],
  bodyY: ["ParamBodyAngleY", "PARAM_BODY_ANGLE_Y"],
  bodyZ: ["ParamBodyAngleZ", "PARAM_BODY_ANGLE_Z"],
  eyeX: ["ParamEyeBallX", "PARAM_EYE_BALL_X"],
  eyeY: ["ParamEyeBallY", "PARAM_EYE_BALL_Y"],
  browY: ["ParamBrowLY", "ParamBrowRY", "PARAM_BROW_L_Y", "PARAM_BROW_R_Y"],
  mouthOpen: ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y"],
  mouthForm: ["ParamMouthForm", "PARAM_MOUTH_FORM"],
};

export const baseMotionBoundary =
  "Base motions only drive common head, body, eye, brow, mouth, and physics-friendly parameters. They do not synthesize missing arms, meshes, textures, or authored poses.";

export const baseMotionProfiles: Partial<Record<DisplayMotion, BaseMotionProfile>> = {
  nod: {
    durationMs: 1180,
    keyframes: [
      {
        atMs: 240,
        targets: [
          { role: "headY", value: 12 },
          { role: "bodyY", value: 1.2 },
        ],
      },
      {
        atMs: 520,
        targets: [
          { role: "headY", value: -5 },
          { role: "bodyY", value: -0.5 },
        ],
      },
      {
        atMs: 780,
        targets: [
          { role: "headY", value: 5.5 },
          { role: "bodyY", value: 0.55 },
        ],
      },
    ],
  },
  point: {
    durationMs: 1320,
    keyframes: [
      {
        atMs: 260,
        targets: [
          { role: "headX", value: -8 },
          { role: "bodyX", value: -1.2 },
          { role: "eyeX", value: -0.18 },
        ],
      },
      {
        atMs: 700,
        targets: [
          { role: "headX", value: -10 },
          { role: "bodyX", value: -1.8 },
          { role: "eyeX", value: -0.28 },
          { role: "browY", value: 0.25 },
        ],
      },
    ],
  },
  wave: {
    durationMs: 1440,
    keyframes: [
      {
        atMs: 240,
        targets: [
          { role: "headZ", value: 7 },
          { role: "bodyZ", value: 1.2 },
          { role: "eyeX", value: 0.12 },
        ],
      },
      {
        atMs: 560,
        targets: [
          { role: "headZ", value: -6 },
          { role: "bodyZ", value: -0.9 },
          { role: "eyeX", value: -0.1 },
        ],
      },
      {
        atMs: 880,
        targets: [
          { role: "headZ", value: 6 },
          { role: "bodyZ", value: 0.9 },
          { role: "eyeX", value: 0.1 },
        ],
      },
    ],
  },
  notify: {
    durationMs: 1120,
    keyframes: [
      {
        atMs: 220,
        targets: [
          { role: "headY", value: -8 },
          { role: "eyeY", value: 0.14 },
          { role: "browY", value: 0.35 },
          { role: "mouthOpen", value: 0.22 },
        ],
      },
      {
        atMs: 580,
        targets: [
          { role: "headY", value: -4 },
          { role: "eyeY", value: 0.08 },
          { role: "browY", value: 0.2 },
          { role: "mouthOpen", value: 0.12 },
        ],
      },
    ],
  },
  speaking: {
    durationMs: 1040,
    keyframes: [
      {
        atMs: 240,
        targets: [
          { role: "headY", value: -2.6 },
          { role: "bodyY", value: -0.25 },
          { role: "mouthForm", value: 0.15 },
        ],
      },
      {
        atMs: 560,
        targets: [
          { role: "headY", value: 1.8 },
          { role: "bodyY", value: 0.18 },
          { role: "mouthForm", value: 0.05 },
        ],
      },
    ],
  },
};

export const baseMotionNames = Object.keys(baseMotionProfiles) as DisplayMotion[];

export function hasBaseMotionProfile(
  motion: DisplayMotion,
): motion is keyof typeof baseMotionProfiles {
  return Object.prototype.hasOwnProperty.call(baseMotionProfiles, motion);
}
