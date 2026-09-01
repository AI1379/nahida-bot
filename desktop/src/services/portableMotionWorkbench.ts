import type { Live2DModelManifest } from "@/domain/live2d";
import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import type {
  PortableMotionAsset,
  PortableMotionTargetModel,
} from "@/domain/portableMotion";
import type {
  NormalizedMotionClip,
  NormalizedPoseChannel,
} from "@/domain/normalizedPose";
import type { Live2DDebugSnapshot } from "@/renderers/live2dRenderer";

import { importMotion3AsPortableAsset } from "./motion3PortableImporter";
import { importMtnAsPortableAsset } from "./mtnPortableImporter";
import { retargetPortableMotion } from "./portableMotionRetargeting";

export const portableMotionWorkbenchMaximumFileSize = 16 * 1024 * 1024;
const maximumCurvePreviewSamples = 240;

export type PortableMotionWorkbenchFormat = "mtn" | "motion3";

export interface PortableMotionWorkbenchLoss {
  id: string;
  reason: string;
}

export interface PortableMotionWorkbenchClamp {
  id: string;
  sampleCount: number;
}

export interface PortableMotionWorkbenchAudit {
  format: PortableMotionWorkbenchFormat;
  sourceItemCount: number;
  importedItemCount: number;
  skipped: PortableMotionWorkbenchLoss[];
  clamped: PortableMotionWorkbenchClamp[];
  assumedRangeParameterIds: string[];
  unsupportedDirectives: string[];
}

export interface PortableMotionWorkbenchCurve {
  channel: NormalizedPoseChannel;
  targetParameterId: string | null;
  minimum: number;
  maximum: number;
  start: number;
  end: number;
  samples: number[];
}

export interface PortableMotionWorkbenchResult {
  fileName: string;
  asset: PortableMotionAsset;
  clip: NormalizedMotionClip | null;
  targetModelId: string;
  compatibilityStatus: "full" | "partial" | "incompatible";
  poseCoverage: number;
  supportedChannels: NormalizedPoseChannel[];
  missingChannels: NormalizedPoseChannel[];
  audit: PortableMotionWorkbenchAudit;
  curves: PortableMotionWorkbenchCurve[];
}

function channelForParameter(
  id: string,
  parameterMap: PortableMotionTargetModel["poseParameterMap"],
): NormalizedPoseChannel | undefined {
  return (Object.entries(parameterMap) as Array<
    [NormalizedPoseChannel, string[]]
  >).find(([, ids]) => ids.includes(id))?.[0];
}

/** Capture exact Cubism Core ranges and semantic mappings for the active model. */
export function createPortableMotionTargetModel(
  manifest: Live2DModelManifest,
  snapshot: Live2DDebugSnapshot,
): PortableMotionTargetModel {
  const profile =
    manifest.performanceProfile ?? createDefaultModelPerformanceProfile(manifest.id);
  const parameters = snapshot.parameters.map((parameter) => ({
    id: parameter.id,
    minimum: parameter.minimum,
    maximum: parameter.maximum,
    defaultValue: parameter.defaultValue,
    channel: channelForParameter(parameter.id, profile.poseParameterMap),
  }));
  return {
    modelId: manifest.id,
    modelName: manifest.name,
    parameterIds: parameters.map((parameter) => parameter.id),
    parameters,
    poseParameterMap: profile.poseParameterMap,
  };
}

function portableAssetId(fileName: string): string {
  const normalized = fileName
    .replace(/\.motion3\.json$/i, "")
    .replace(/\.mtn$/i, "")
    .replace(/[^a-z0-9_-]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  return normalized || "imported-motion";
}

function targetParameterId(
  channel: NormalizedPoseChannel,
  target: PortableMotionTargetModel,
): string | null {
  const ids = new Set(target.parameterIds);
  return target.poseParameterMap[channel].find((id) => ids.has(id)) ?? null;
}

function curveForChannel(
  asset: PortableMotionAsset,
  channel: NormalizedPoseChannel,
  target: PortableMotionTargetModel,
): PortableMotionWorkbenchCurve {
  const samples = asset.frames.flatMap((frame) =>
    typeof frame[channel] === "number" ? [frame[channel]] : [],
  );
  if (!samples.length) throw new Error(`${asset.id} has no samples for ${channel}`);
  const previewSamples = samples.length <= maximumCurvePreviewSamples
    ? samples
    : Array.from({ length: maximumCurvePreviewSamples }, (_, index) =>
        samples[
          Math.round(
            (index * (samples.length - 1)) / (maximumCurvePreviewSamples - 1),
          )
        ],
      );
  return {
    channel,
    targetParameterId: targetParameterId(channel, target),
    minimum: Math.min(...samples),
    maximum: Math.max(...samples),
    start: samples[0],
    end: samples.at(-1) ?? samples[0],
    samples: previewSamples,
  };
}

function importMtn(
  fileName: string,
  text: string,
): { asset: PortableMotionAsset; audit: PortableMotionWorkbenchAudit } {
  const imported = importMtnAsPortableAsset(text, {
    assetId: portableAssetId(fileName),
    name: fileName,
  });
  return {
    asset: imported.asset,
    audit: {
      format: "mtn",
      sourceItemCount: imported.report.totalParameters,
      importedItemCount: imported.report.importedParameters,
      skipped: imported.report.skippedParameters,
      clamped: imported.report.clampedParameters,
      assumedRangeParameterIds: imported.report.assumedRangeParameterIds,
      unsupportedDirectives: imported.report.unsupportedDirectives,
    },
  };
}

function parsedMotion3(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`invalid motion3 JSON: ${message}`, { cause: error });
  }
}

function importMotion3(
  fileName: string,
  text: string,
  target: PortableMotionTargetModel,
): { asset: PortableMotionAsset; audit: PortableMotionWorkbenchAudit } {
  const imported = importMotion3AsPortableAsset(parsedMotion3(text), {
    assetId: portableAssetId(fileName),
    name: fileName,
    sourceModelId: target.modelId,
    sourceParameters: target.parameters,
  });
  return {
    asset: imported.asset,
    audit: {
      format: "motion3",
      sourceItemCount: imported.report.totalCurves,
      importedItemCount: imported.report.importedCurves,
      skipped: imported.report.skippedCurves.map((curve) => ({
        id: curve.id,
        reason: curve.reason,
      })),
      clamped: [],
      assumedRangeParameterIds: [],
      unsupportedDirectives: [],
    },
  };
}

function formatForFileName(fileName: string): PortableMotionWorkbenchFormat {
  const normalized = fileName.toLowerCase();
  if (normalized.endsWith(".mtn")) return "mtn";
  if (normalized.endsWith(".motion3.json") || normalized.endsWith(".json")) {
    return "motion3";
  }
  throw new Error("choose a .mtn or .motion3.json file");
}

/** Analyze a local motion file and project its portable channels onto a model. */
export function importPortableMotionForWorkbench(
  source: { fileName: string; text: string; size?: number },
  target: PortableMotionTargetModel,
): PortableMotionWorkbenchResult {
  if (
    source.size !== undefined &&
    source.size > portableMotionWorkbenchMaximumFileSize
  ) {
    throw new Error("motion file exceeds the 16 MiB Workbench limit");
  }
  const imported = formatForFileName(source.fileName) === "mtn"
    ? importMtn(source.fileName, source.text)
    : importMotion3(source.fileName, source.text, target);
  const retargeted = retargetPortableMotion(imported.asset, target);
  return {
    fileName: source.fileName,
    asset: imported.asset,
    clip: retargeted.clip,
    targetModelId: target.modelId,
    compatibilityStatus: retargeted.compatibility.status,
    poseCoverage: retargeted.compatibility.poseCoverage,
    supportedChannels: retargeted.compatibility.supportedChannels,
    missingChannels: retargeted.compatibility.missingChannels,
    audit: imported.audit,
    curves: imported.asset.channels.map((channel) =>
      curveForChannel(imported.asset, channel, target),
    ),
  };
}

/** SVG polyline coordinates for a normalized channel preview. */
export function portableMotionSparklinePoints(
  samples: number[],
  width = 180,
  height = 42,
): string {
  if (!samples.length) return "";
  const minimum = Math.min(...samples);
  const maximum = Math.max(...samples);
  const span = maximum - minimum;
  return samples.map((sample, index) => {
    const x = samples.length === 1 ? width / 2 : (index * width) / (samples.length - 1);
    const normalized = span > 0 ? (sample - minimum) / span : 0.5;
    const y = height - normalized * height;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
}
