import type {
  PortableMotionAsset,
  PortableMotionCompatibilityReport,
  PortableMotionFeatureCue,
  PortableMotionRetargetProfile,
} from "@/domain/portableMotion";
import {
  createNormalizedPoseFrame,
  type NormalizedMotionClip,
} from "@/domain/normalizedPose";

/** Options for compatibility-safe projection onto a target model profile. */
export interface RetargetPortableMotionOptions {
  clipId?: string;
  intentId?: string;
}

/**
 * Target-safe pose output and the feature cues whose semantic bindings exist.
 * Feature execution remains a separate runtime concern.
 */
export interface RetargetedPortableMotion {
  clip: NormalizedMotionClip | null;
  featureCues: PortableMotionFeatureCue[];
  compatibility: PortableMotionCompatibilityReport;
}

function coverage(supported: number, total: number): number {
  return total === 0 ? 1 : supported / total;
}

/** Compare portable pose/features with concrete target-model bindings. */
export function analyzePortableMotionCompatibility(
  asset: PortableMotionAsset,
  target: PortableMotionRetargetProfile,
): PortableMotionCompatibilityReport {
  const parameterIds = new Set(target.parameterIds);
  const featureIds = new Set(target.supportedFeatureIds ?? []);
  const supportedChannels = asset.channels.filter((channel) =>
    target.poseParameterMap[channel].some((id) => parameterIds.has(id)),
  );
  const missingChannels = asset.channels.filter(
    (channel) => !supportedChannels.includes(channel),
  );
  const requestedFeatureIds = [
    ...new Set(asset.features.map((cue) => cue.featureId)),
  ];
  const supportedFeatureIds = requestedFeatureIds.filter((id) => featureIds.has(id));
  const missingFeatureIds = requestedFeatureIds.filter((id) => !featureIds.has(id));
  const supportedCount = supportedChannels.length + supportedFeatureIds.length;
  const requestedCount = asset.channels.length + requestedFeatureIds.length;
  return {
    modelId: target.modelId,
    status:
      supportedCount === 0 && requestedCount > 0
        ? "incompatible"
        : supportedCount === requestedCount
          ? "full"
          : "partial",
    poseCoverage: coverage(supportedChannels.length, asset.channels.length),
    featureCoverage: coverage(supportedFeatureIds.length, requestedFeatureIds.length),
    supportedChannels,
    missingChannels,
    supportedFeatureIds,
    missingFeatureIds,
  };
}

/** Project supported pose channels and cues without leaking source-model IDs. */
export function retargetPortableMotion(
  asset: PortableMotionAsset,
  target: PortableMotionRetargetProfile,
  options: RetargetPortableMotionOptions = {},
): RetargetedPortableMotion {
  const compatibility = analyzePortableMotionCompatibility(asset, target);
  const supported = new Set(compatibility.supportedChannels);
  const clip: NormalizedMotionClip | null = supported.size
    ? {
        id: options.clipId ?? `${asset.id}:${target.modelId}`,
        intentId: options.intentId ?? asset.id,
        durationMs: asset.durationMs,
        loopable: asset.loopable,
        restoreAtEnd: asset.restoreAtEnd,
        channels: compatibility.supportedChannels,
        frames: asset.frames.map((source) => {
          const values = Object.fromEntries(
            compatibility.supportedChannels.flatMap((channel) =>
              typeof source[channel] === "number" ? [[channel, source[channel]]] : [],
            ),
          );
          return createNormalizedPoseFrame(source.atMs, values);
        }),
      }
    : null;
  const supportedFeatures = new Set(compatibility.supportedFeatureIds);
  return {
    clip,
    featureCues: asset.features.filter((cue) => supportedFeatures.has(cue.featureId)),
    compatibility,
  };
}
