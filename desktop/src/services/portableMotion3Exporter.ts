import type {
  PortableMotionAsset,
  PortableMotionSourceParameter,
} from "@/domain/portableMotion";
import type { ModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import type { NormalizedPoseChannel } from "@/domain/normalizedPose";

import {
  denormalizePortableMotionValue,
  portableMotionSourceBindings,
  type PortableMotionSourceBinding,
} from "./portableMotionSource";

export interface PortableMotion3ExportOptions {
  targetParameters: PortableMotionSourceParameter[];
  poseParameterMap: ModelPerformanceProfile["poseParameterMap"];
  fps?: number;
}

export interface PortableMotion3Curve {
  Target: "Parameter";
  Id: string;
  FadeInTime?: number;
  FadeOutTime?: number;
  Segments: number[];
}

export interface PortableMotion3Document {
  Version: 3;
  Meta: {
    Duration: number;
    Fps: number;
    Loop: boolean;
    AreBeziersRestricted: true;
    FadeInTime?: number;
    FadeOutTime?: number;
    CurveCount: number;
    TotalSegmentCount: number;
    TotalPointCount: number;
    UserDataCount: 0;
    TotalUserDataSize: 0;
  };
  Curves: PortableMotion3Curve[];
}

function fadeSeconds(value: number | undefined, label: string): number | undefined {
  if (value === undefined) return undefined;
  if (!Number.isFinite(value) || value < 0) {
    throw new Error(`${label} must be a non-negative finite number`);
  }
  return value / 1000;
}

function motion3FadeFields(
  fadeInMs: number | undefined,
  fadeOutMs: number | undefined,
  label: string,
): Pick<PortableMotion3Curve, "FadeInTime" | "FadeOutTime"> {
  const fadeIn = fadeSeconds(fadeInMs, `${label} fade-in`);
  const fadeOut = fadeSeconds(fadeOutMs, `${label} fade-out`);
  return {
    ...(fadeIn === undefined ? {} : { FadeInTime: fadeIn }),
    ...(fadeOut === undefined ? {} : { FadeOutTime: fadeOut }),
  };
}

export interface PortableMotion3ExportReport {
  exportedChannels: NormalizedPoseChannel[];
  missingChannels: NormalizedPoseChannel[];
  omittedFeatureIds: string[];
}

export interface PortableMotion3ExportResult {
  document: PortableMotion3Document;
  report: PortableMotion3ExportReport;
}

function targetForChannel(
  channel: NormalizedPoseChannel,
  bindings: Map<string, PortableMotionSourceBinding>,
  parameterMap: ModelPerformanceProfile["poseParameterMap"],
): PortableMotionSourceBinding | null {
  for (const id of parameterMap[channel]) {
    const binding = bindings.get(id);
    if (binding) return binding;
  }
  return null;
}

function inferredFps(asset: PortableMotionAsset): number {
  if (asset.frames.length < 2) return 30;
  const intervalMs = asset.frames[1].atMs - asset.frames[0].atMs;
  if (intervalMs <= 0) return 30;
  const rawFps = Math.max(1, Math.min(120, 1000 / intervalMs));
  const integralFps = Math.round(rawFps);
  return Math.abs(rawFps - integralFps) < 1e-6 ? integralFps : rawFps;
}

function curveSegments(
  asset: PortableMotionAsset,
  channel: NormalizedPoseChannel,
  target: PortableMotionSourceBinding,
): number[] {
  return asset.frames.flatMap((frame, index) => {
    const value = frame[channel];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`${asset.id} frame ${index} is missing ${channel}`);
    }
    const point = [frame.atMs / 1000, denormalizePortableMotionValue(channel, value, target)];
    return index === 0 ? point : [0, ...point];
  });
}

/** Export a target-specific Cubism 3 motion using lossless linear frame samples. */
export function exportPortableMotionAsMotion3(
  asset: PortableMotionAsset,
  options: PortableMotion3ExportOptions,
): PortableMotion3ExportResult {
  if (!asset.frames.length) throw new Error("portable motion has no frames");
  const bindings = portableMotionSourceBindings(options.targetParameters);
  const curves: PortableMotion3Curve[] = [];
  const exportedChannels: NormalizedPoseChannel[] = [];
  const missingChannels: NormalizedPoseChannel[] = [];
  const channelFades = new Map(
    (asset.channelFades ?? []).map((fade) => [fade.channel, fade]),
  );
  for (const channel of asset.channels) {
    const target = targetForChannel(channel, bindings, options.poseParameterMap);
    if (!target) {
      missingChannels.push(channel);
      continue;
    }
    const fade = channelFades.get(channel);
    curves.push({
      Target: "Parameter",
      Id: target.id,
      ...motion3FadeFields(fade?.fadeInMs, fade?.fadeOutMs, channel),
      Segments: curveSegments(asset, channel, target),
    });
    exportedChannels.push(channel);
  }
  const segmentCount = curves.length * Math.max(asset.frames.length - 1, 0);
  return {
    document: {
      Version: 3,
      Meta: {
        Duration: asset.durationMs / 1000,
        Fps: options.fps ?? inferredFps(asset),
        Loop: asset.loopable,
        AreBeziersRestricted: true,
        ...motion3FadeFields(asset.fadeInMs, asset.fadeOutMs, "motion"),
        CurveCount: curves.length,
        TotalSegmentCount: segmentCount,
        TotalPointCount: curves.length * asset.frames.length,
        UserDataCount: 0,
        TotalUserDataSize: 0,
      },
      Curves: curves,
    },
    report: {
      exportedChannels,
      missingChannels,
      omittedFeatureIds: [
        ...new Set(asset.features.map((feature) => feature.featureId)),
      ],
    },
  };
}
