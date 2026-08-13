import type {
  MotionEmotion,
  MotionIntent,
  MotionIntentName,
} from "./motionIntent";
import type { MotionValidationWarning } from "./motionPlan";
import type { MotionPlan } from "./motionPlan";
import type { NormalizedMotionClip } from "./normalizedPose";

export const motionDatasetKinds = [
  "decisions",
  "executions",
  "preferences",
  "invalid",
] as const;

export type MotionDatasetKind = (typeof motionDatasetKinds)[number];

export type MotionPlaybackSurface =
  | "pet"
  | "runtime"
  | "workbench"
  | "debug"
  | "legacy";

interface MotionDatasetRecordBase {
  schemaVersion: 1;
  timestamp: string;
}

export interface MotionDecisionRecord extends MotionDatasetRecordBase {
  type: "motion_decision";
  decisionId: string;
  assistantText: string;
  runtimeStatus: string;
  selectedIntent: MotionIntent;
  source: MotionIntent["source"];
  modelId: string;
  modelProfileVersion: string;
  plannerVersion: string;
  cacheHit: boolean;
  playbackSurface?: MotionPlaybackSurface;
}

export interface MotionExecutionRecord extends MotionDatasetRecordBase {
  type: "motion_execution";
  decisionId: string;
  motionPlanId: string;
  intent: MotionIntent;
  modelId: string;
  modelProfileVersion: string;
  driverVersion: string;
  synthesizerVersion: string;
  validatorVersion: string;
  mixerVersion: string;
  primitive: string;
  durationMs: number;
  frameCount: number;
  validationStatus: "accepted" | "corrected" | "rejected";
  validationWarnings: MotionValidationWarning[];
  fallbackUsed: boolean;
  motionPlan: MotionPlan;
  normalizedClip: NormalizedMotionClip;
  playbackSurface?: MotionPlaybackSurface;
}

export interface MotionPlaybackSummary {
  schemaVersion: 1;
  timestamp: string;
  decisionId: string;
  motionPlanId: string;
  assistantText: string;
  surface: MotionPlaybackSurface;
  intent: MotionIntent;
  modelId: string;
  primitive: string;
  validationStatus: MotionExecutionRecord["validationStatus"];
  fallbackUsed: boolean;
  motionPlan: MotionPlan;
  normalizedClip: NormalizedMotionClip;
}

export type MotionPreferenceLabel =
  | "good"
  | "bad"
  | "too_much"
  | "too_little"
  | "wrong_emotion"
  | "repetitive"
  | "more_natural"
  | "better_timing";

export interface MotionPreferenceCorrection {
  intent?: MotionIntentName;
  emotion?: MotionEmotion;
  intensity?: number;
}

export interface MotionPreferenceRecord extends MotionDatasetRecordBase {
  type: "motion_preference";
  preferenceId: string;
  assistantText: string;
  candidateA: string;
  candidateB?: string;
  winner?: string;
  labels: MotionPreferenceLabel[];
  notes?: string;
  correction?: MotionPreferenceCorrection;
  playbackSurface?: MotionPlaybackSurface;
}

export interface MotionPreferenceRetractionRecord
  extends MotionDatasetRecordBase {
  type: "motion_preference_retraction";
  retractionId: string;
  retractsPreferenceId: string;
  motionPlanId: string;
}

export type MotionPreferenceDatasetRecord =
  | MotionPreferenceRecord
  | MotionPreferenceRetractionRecord;

export interface MotionInvalidRecord extends MotionDatasetRecordBase {
  type: "motion_invalid";
  decisionId: string;
  assistantText: string;
  reason: string;
  details: Record<string, unknown>;
  fallbackPlan?: string;
}

export interface MotionDatasetRecords {
  decisions: MotionDecisionRecord;
  executions: MotionExecutionRecord;
  preferences: MotionPreferenceDatasetRecord;
  invalid: MotionInvalidRecord;
}

export type MotionDatasetRecord = MotionDatasetRecords[MotionDatasetKind];

export type MotionDatasetExport = Record<MotionDatasetKind, string>;
