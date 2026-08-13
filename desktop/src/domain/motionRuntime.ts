import type { ModelPerformanceProfile } from "./modelPerformanceProfile";
import type { MotionIntent } from "./motionIntent";
import type { MotionPlan, MotionValidationWarning } from "./motionPlan";
import type {
  MotionDecisionRecord,
  MotionExecutionRecord,
  MotionInvalidRecord,
  MotionPreferenceRecord,
  MotionPreferenceRetractionRecord,
} from "./motionTelemetry";
import type {
  NormalizedMotionClip,
  NormalizedPoseValues,
} from "./normalizedPose";
import type { PetRuntimeStatus } from "./runtime";

export interface MotionPlannerInput {
  assistantText: string;
  segmentIndex: number;
  totalSegments: number;
  displayEmotion?: string;
  runtimeStatus: PetRuntimeStatus;
  currentPoseSummary: Partial<NormalizedPoseValues>;
  previousIntent?: MotionIntent;
  recentIntents: MotionIntent[];
  speechDurationEstimateMs?: number;
}

export interface MotionPlanner {
  readonly id: string;
  readonly version: string;
  plan(input: MotionPlannerInput): Promise<MotionIntent>;
}

export interface MotionSynthesisContext {
  previousPose: Readonly<NormalizedPoseValues>;
  audioEnergy: number;
  modelProfile: ModelPerformanceProfile;
}

export interface MotionSynthesizer {
  readonly id: string;
  readonly version: string;
  synthesize(
    intent: MotionIntent,
    context: MotionSynthesisContext,
  ): Promise<MotionPlan>;
}

export interface MotionValidationContext {
  modelProfile: ModelPerformanceProfile;
  primitive?: string;
  expression?: string;
}

export type MotionValidationStatus = "accepted" | "corrected" | "rejected";

export interface MotionValidationResult {
  status: MotionValidationStatus;
  clip: NormalizedMotionClip | null;
  warnings: MotionValidationWarning[];
}

export interface MotionValidator {
  readonly id: string;
  readonly version: string;
  validate(
    clip: NormalizedMotionClip,
    context: MotionValidationContext,
  ): MotionValidationResult;
}

export type MotionLayerSource =
  | "debug"
  | "state-transition"
  | "safety"
  | "lip-sync"
  | "speech"
  | "expression"
  | "idle";

export interface MotionLayer {
  id: string;
  source: MotionLayerSource;
  sequence: number;
  clip: NormalizedMotionClip;
}

export interface MotionMixer {
  readonly id: string;
  readonly version: string;
  mix(layers: MotionLayer[]): NormalizedMotionClip | null;
}

export interface MotionCacheEntry {
  key: string;
  plannerVersion: string;
  modelProfileVersion: string;
  primitiveVersion: string;
  intent: MotionIntent;
  plan?: MotionPlan;
  createdAt: string;
}

export interface MotionCache {
  get(key: string): Promise<MotionCacheEntry | null>;
  set(entry: MotionCacheEntry): Promise<void>;
  clear(): Promise<void>;
}

export interface MotionTelemetry {
  recordDecision(record: MotionDecisionRecord): Promise<void>;
  recordExecution(record: MotionExecutionRecord): Promise<void>;
  recordInvalid(record: MotionInvalidRecord): Promise<void>;
}

export interface MotionPreferenceStore {
  record(record: MotionPreferenceRecord): Promise<void>;
  retract(record: MotionPreferenceRetractionRecord): Promise<void>;
}
