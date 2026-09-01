import type {
  NormalizedPoseChannel,
  NormalizedPoseValues,
} from "./normalizedPose";

export const portableMotionSchemaVersion = 1 as const;

export type PortableMotionSourceFormat = "motion3" | "mtn" | "manual";

export interface PortableMotionSource {
  format: PortableMotionSourceFormat;
  importerVersion: string;
  modelId?: string;
  name?: string;
}

/** Parameter bounds and optional semantic binding captured from a source model. */
export interface PortableMotionSourceParameter {
  id: string;
  minimum: number;
  maximum: number;
  defaultValue: number;
  channel?: NormalizedPoseChannel;
}

export type PortableMotionPoseFrame = {
  atMs: number;
} & Partial<NormalizedPoseValues>;

export type PortableMotionFeatureValue = boolean | number | string;

/** A model capability cue that may be ignored when the target lacks a binding. */
export interface PortableMotionFeatureCue {
  atMs: number;
  featureId: string;
  value: PortableMotionFeatureValue;
  durationMs?: number;
}

/** Optional blend timing retained without carrying source-model parameter IDs. */
export interface PortableMotionChannelFade {
  channel: NormalizedPoseChannel;
  fadeInMs?: number;
  fadeOutMs?: number;
}

/**
 * Persistent, model-independent motion data. Continuous pose channels are
 * normalized; model-specific actions remain optional semantic feature cues.
 */
export interface PortableMotionAsset {
  schemaVersion: typeof portableMotionSchemaVersion;
  id: string;
  name: string;
  durationMs: number;
  fadeInMs?: number;
  fadeOutMs?: number;
  loopable: boolean;
  restoreAtEnd: boolean;
  channels: NormalizedPoseChannel[];
  frames: PortableMotionPoseFrame[];
  channelFades?: PortableMotionChannelFade[];
  features: PortableMotionFeatureCue[];
  source: PortableMotionSource;
}

export interface PortableMotionRetargetProfile {
  modelId: string;
  parameterIds: string[];
  poseParameterMap: Record<NormalizedPoseChannel, string[]>;
  supportedFeatureIds?: string[];
}

/** Live target snapshot used by Workbench import and preview tooling. */
export interface PortableMotionTargetModel extends PortableMotionRetargetProfile {
  modelName: string;
  parameters: PortableMotionSourceParameter[];
}

export type PortableMotionCompatibilityStatus =
  | "full"
  | "partial"
  | "incompatible";

export interface PortableMotionCompatibilityReport {
  modelId: string;
  status: PortableMotionCompatibilityStatus;
  poseCoverage: number;
  featureCoverage: number;
  supportedChannels: NormalizedPoseChannel[];
  missingChannels: NormalizedPoseChannel[];
  supportedFeatureIds: string[];
  missingFeatureIds: string[];
}
