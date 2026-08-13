export interface NormalizedPoseFrame {
  atMs: number;
  headYaw: number;
  headPitch: number;
  headRoll: number;
  bodyYaw: number;
  bodyPitch: number;
  bodyRoll: number;
  gazeX: number;
  gazeY: number;
  browUpLeft: number;
  browUpRight: number;
  eyeOpenLeft: number;
  eyeOpenRight: number;
  mouthOpen: number;
  mouthSmile: number;
  breath: number;
  energy: number;
}

export type NormalizedPoseValues = Omit<NormalizedPoseFrame, "atMs">;

export const normalizedPoseChannels = [
  "headYaw",
  "headPitch",
  "headRoll",
  "bodyYaw",
  "bodyPitch",
  "bodyRoll",
  "gazeX",
  "gazeY",
  "browUpLeft",
  "browUpRight",
  "eyeOpenLeft",
  "eyeOpenRight",
  "mouthOpen",
  "mouthSmile",
  "breath",
  "energy",
] as const satisfies readonly (keyof NormalizedPoseValues)[];

export type NormalizedPoseChannel = (typeof normalizedPoseChannels)[number];

export interface NormalizedPoseChannelRange {
  minimum: number;
  maximum: number;
}

const signedChannelRange = Object.freeze({ minimum: -1, maximum: 1 });
const unitChannelRange = Object.freeze({ minimum: 0, maximum: 1 });

export const normalizedPoseChannelRanges = {
  headYaw: signedChannelRange,
  headPitch: signedChannelRange,
  headRoll: signedChannelRange,
  bodyYaw: signedChannelRange,
  bodyPitch: signedChannelRange,
  bodyRoll: signedChannelRange,
  gazeX: signedChannelRange,
  gazeY: signedChannelRange,
  browUpLeft: signedChannelRange,
  browUpRight: signedChannelRange,
  eyeOpenLeft: unitChannelRange,
  eyeOpenRight: unitChannelRange,
  mouthOpen: unitChannelRange,
  mouthSmile: signedChannelRange,
  breath: unitChannelRange,
  energy: unitChannelRange,
} as const satisfies Record<NormalizedPoseChannel, NormalizedPoseChannelRange>;

export const neutralNormalizedPose = Object.freeze({
  headYaw: 0,
  headPitch: 0,
  headRoll: 0,
  bodyYaw: 0,
  bodyPitch: 0,
  bodyRoll: 0,
  gazeX: 0,
  gazeY: 0,
  browUpLeft: 0,
  browUpRight: 0,
  eyeOpenLeft: 1,
  eyeOpenRight: 1,
  mouthOpen: 0,
  mouthSmile: 0,
  breath: 0,
  energy: 0,
}) satisfies Readonly<NormalizedPoseValues>;

export interface NormalizedMotionClip {
  id: string;
  intentId: string;
  durationMs: number;
  loopable: boolean;
  restoreAtEnd: boolean;
  /** Canonical channels actively controlled by this clip. */
  channels: NormalizedPoseChannel[];
  frames: NormalizedPoseFrame[];
}

export function createNormalizedPoseFrame(
  atMs: number,
  values: Partial<NormalizedPoseValues> = {},
): NormalizedPoseFrame {
  return {
    atMs,
    ...neutralNormalizedPose,
    ...values,
  };
}
