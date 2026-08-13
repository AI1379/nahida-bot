import type { MotionIntent } from "./motionIntent";
import {
  neutralNormalizedPose,
  type NormalizedMotionClip,
  type NormalizedPoseValues,
} from "./normalizedPose";
import type { PetRuntimeStatus } from "./runtime";

export type MotionDriverPhase = "enter" | "sustain" | "exit";

export interface MotionDriverContext {
  runtimeStatus: PetRuntimeStatus;
  modelId?: string;
  motionHint?: string;
  segmentIndex?: number;
  totalSegments?: number;
}

export interface MotionDriverInput {
  intent: MotionIntent;
  phase: MotionDriverPhase;
  previousPose: Readonly<NormalizedPoseValues>;
  /** Normalized speech energy in the inclusive range 0..1. */
  audioEnergy: number;
  /** Amount of future motion the driver should produce. */
  lookaheadMs: number;
  context: MotionDriverContext;
}

export interface MotionDriverWarning {
  code: string;
  message: string;
}

export interface MotionDriverResult {
  clip: NormalizedMotionClip | null;
  primitive?: string;
  warnings: MotionDriverWarning[];
}

export interface MotionDriver {
  readonly id: string;
  readonly version: string;
  drive(input: MotionDriverInput): Promise<MotionDriverResult>;
}

export const motionDriverDefaults = {
  phase: "enter",
  audioEnergy: 0,
  lookaheadMs: 200,
} as const satisfies Pick<
  MotionDriverInput,
  "phase" | "audioEnergy" | "lookaheadMs"
>;

export interface CreateMotionDriverInputOptions {
  intent: MotionIntent;
  phase?: MotionDriverPhase;
  previousPose?: Partial<NormalizedPoseValues>;
  audioEnergy?: number;
  lookaheadMs?: number;
  context: MotionDriverContext;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

export function createMotionDriverInput(
  options: CreateMotionDriverInputOptions,
): MotionDriverInput {
  const audioEnergy =
    typeof options.audioEnergy === "number" &&
    Number.isFinite(options.audioEnergy)
    ? options.audioEnergy
    : motionDriverDefaults.audioEnergy;
  const lookaheadMs =
    typeof options.lookaheadMs === "number" &&
    Number.isFinite(options.lookaheadMs)
    ? options.lookaheadMs
    : motionDriverDefaults.lookaheadMs;

  return {
    intent: options.intent,
    phase: options.phase ?? motionDriverDefaults.phase,
    previousPose: {
      ...neutralNormalizedPose,
      ...options.previousPose,
    },
    audioEnergy: clamp(audioEnergy, 0, 1),
    lookaheadMs: Math.max(1, lookaheadMs),
    context: { ...options.context },
  };
}
