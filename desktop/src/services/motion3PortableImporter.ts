import {
  portableMotionSchemaVersion,
  type PortableMotionAsset,
  type PortableMotionChannelFade,
  type PortableMotionPoseFrame,
  type PortableMotionSourceParameter,
} from "@/domain/portableMotion";
import {
  normalizedPoseChannels,
  type NormalizedPoseChannel,
} from "@/domain/normalizedPose";

import {
  motion3CurveValueAt,
  parseMotion3Segments,
  type ParsedMotion3Curve,
} from "./motion3Curve";
import {
  nonEmptyPortableMotionString,
  normalizePortableMotionSourceValue,
  portableMotionSourceBindings,
  type PortableMotionSourceBinding,
} from "./portableMotionSource";

/** Limits keep future user-supplied model packs bounded before UI integration. */
const importerVersion = "motion3-portable-v1";
const maximumCurveCount = 4_096;
const maximumSegmentValueCount = 1_000_000;
const maximumDurationMs = 300_000;
const maximumSampleRateFps = 120;

/**
 * Parameter bounds captured from the source model's Cubism Core instance.
 * Non-standard IDs require an explicit canonical channel assignment.
 */
export type Motion3SourceParameter = PortableMotionSourceParameter;

/** Import identity, sampling, and source-model parameter metadata. */
export interface Motion3PortableImportOptions {
  assetId: string;
  name?: string;
  sourceModelId?: string;
  sourceName?: string;
  sourceParameters: Motion3SourceParameter[];
  sampleRateFps?: number;
  restoreAtEnd?: boolean;
}

export type Motion3SkippedCurveReason =
  | "unsupported_target"
  | "unknown_parameter"
  | "unmapped_parameter"
  | "duplicate_channel";

/** A source curve deliberately omitted from the portable pose timeline. */
export interface Motion3SkippedCurve {
  curveIndex: number;
  target: string;
  id: string;
  reason: Motion3SkippedCurveReason;
}

/** Audit data for compatibility UI and import troubleshooting. */
export interface Motion3PortableImportReport {
  totalCurves: number;
  parameterCurves: number;
  importedCurves: number;
  importedChannels: NormalizedPoseChannel[];
  skippedCurves: Motion3SkippedCurve[];
}

/** Portable output plus the loss report retained for later user review. */
export interface Motion3PortableImportResult {
  asset: PortableMotionAsset;
  report: Motion3PortableImportReport;
}

interface ImportedCurve {
  channel: NormalizedPoseChannel;
  source: PortableMotionSourceBinding;
  curve: ParsedMotion3Curve;
  fadeInMs?: number;
  fadeOutMs?: number;
}

interface Motion3Metadata {
  durationMs: number;
  fps: number;
  loopable: boolean;
  fadeInMs?: number;
  fadeOutMs?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function optionalFadeMs(value: unknown, label: string): number | undefined {
  if (value === undefined) return undefined;
  const milliseconds = finiteNumber(value, label) * 1000;
  if (milliseconds < 0 || milliseconds > maximumDurationMs) {
    throw new Error(`${label} must be between 0 and ${maximumDurationMs} ms`);
  }
  return milliseconds;
}

function motion3Metadata(
  document: Record<string, unknown>,
  requestedSampleRate: number | undefined,
): Motion3Metadata {
  if (document.Version !== 3) throw new Error("only motion3 Version 3 is supported");
  if (!isRecord(document.Meta)) throw new Error("motion3 Meta is required");
  const durationMs = finiteNumber(document.Meta.Duration, "motion3 duration") * 1000;
  if (durationMs <= 0 || durationMs > maximumDurationMs) {
    throw new Error(`motion3 duration must be between 0 and ${maximumDurationMs} ms`);
  }
  const sourceFps = finiteNumber(document.Meta.Fps, "motion3 fps");
  const fps = requestedSampleRate ?? sourceFps;
  if (fps < 1 || fps > maximumSampleRateFps) {
    throw new Error(`sample rate must be between 1 and ${maximumSampleRateFps} fps`);
  }
  return {
    durationMs,
    fps,
    loopable: document.Meta.Loop === true,
    fadeInMs: optionalFadeMs(document.Meta.FadeInTime, "motion3 fade-in"),
    fadeOutMs: optionalFadeMs(document.Meta.FadeOutTime, "motion3 fade-out"),
  };
}

function curveIdentity(value: unknown, curveIndex: number): {
  target: string;
  id: string;
  segments: unknown[];
  fadeInMs?: number;
  fadeOutMs?: number;
} {
  if (!isRecord(value)) throw new Error(`motion3 curve ${curveIndex} must be an object`);
  const target = nonEmptyPortableMotionString(
    value.Target,
    `curve ${curveIndex} target`,
  );
  const id = nonEmptyPortableMotionString(value.Id, `curve ${curveIndex} id`);
  if (!Array.isArray(value.Segments)) {
    throw new Error(`curve ${curveIndex} Segments must be an array`);
  }
  return {
    target,
    id,
    segments: value.Segments,
    fadeInMs: optionalFadeMs(value.FadeInTime, `curve ${curveIndex} fade-in`),
    fadeOutMs: optionalFadeMs(value.FadeOutTime, `curve ${curveIndex} fade-out`),
  };
}

function importCurves(
  values: unknown[],
  bindings: Map<string, PortableMotionSourceBinding>,
): { curves: ImportedCurve[]; skipped: Motion3SkippedCurve[] } {
  const curves: ImportedCurve[] = [];
  const skipped: Motion3SkippedCurve[] = [];
  const importedChannels = new Set<NormalizedPoseChannel>();
  let segmentValueCount = 0;

  values.forEach((value, curveIndex) => {
    const { target, id, segments, fadeInMs, fadeOutMs } = curveIdentity(
      value,
      curveIndex,
    );
    segmentValueCount += segments.length;
    if (segmentValueCount > maximumSegmentValueCount) {
      throw new Error("motion3 segment data exceeds the import limit");
    }
    let reason: Motion3SkippedCurveReason | null = null;
    const binding = bindings.get(id);
    if (target !== "Parameter") reason = "unsupported_target";
    else if (!binding) reason = "unknown_parameter";
    else if (!binding.channel) reason = "unmapped_parameter";
    else if (importedChannels.has(binding.channel)) reason = "duplicate_channel";

    if (reason) {
      skipped.push({ curveIndex, target, id, reason });
      return;
    }
    if (!binding?.channel) return;
    let parsedCurve: ParsedMotion3Curve;
    try {
      parsedCurve = parseMotion3Segments(segments);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(`curve ${curveIndex} (${id}) is invalid: ${message}`, {
        cause: error,
      });
    }
    curves.push({
      channel: binding.channel,
      source: binding,
      curve: parsedCurve,
      fadeInMs,
      fadeOutMs,
    });
    importedChannels.add(binding.channel);
  });
  return { curves, skipped };
}

function importedChannelFades(curves: ImportedCurve[]): PortableMotionChannelFade[] {
  return curves.flatMap((curve) =>
    curve.fadeInMs === undefined && curve.fadeOutMs === undefined
      ? []
      : [{
          channel: curve.channel,
          fadeInMs: curve.fadeInMs,
          fadeOutMs: curve.fadeOutMs,
        }],
  );
}

function sampleTimes(durationMs: number, fps: number): number[] {
  const intervalMs = 1000 / fps;
  const frameCount = Math.max(2, Math.ceil(durationMs / intervalMs) + 1);
  return Array.from({ length: frameCount }, (_, index) =>
    index === frameCount - 1 ? durationMs : Math.min(index * intervalMs, durationMs),
  );
}

function sampleFrames(
  curves: ImportedCurve[],
  metadata: Motion3Metadata,
): PortableMotionPoseFrame[] {
  return sampleTimes(metadata.durationMs, metadata.fps).map((atMs) => {
    const frame: PortableMotionPoseFrame = { atMs };
    for (const imported of curves) {
      frame[imported.channel] = normalizePortableMotionSourceValue(
        imported.channel,
        motion3CurveValueAt(imported.curve, atMs / 1000),
        imported.source,
      );
    }
    return frame;
  });
}

/**
 * Convert a Cubism 3 motion document into normalized, model-independent pose
 * frames. Source ranges must come from the source model rather than curve
 * extrema so that a subtle motion does not get expanded to full intensity.
 */
export function importMotion3AsPortableAsset(
  value: unknown,
  options: Motion3PortableImportOptions,
): Motion3PortableImportResult {
  if (!isRecord(value)) throw new Error("motion3 document must be an object");
  const assetId = nonEmptyPortableMotionString(options.assetId, "asset id");
  const metadata = motion3Metadata(value, options.sampleRateFps);
  if (!Array.isArray(value.Curves)) throw new Error("motion3 Curves must be an array");
  if (value.Curves.length > maximumCurveCount) {
    throw new Error(`motion3 curve count exceeds ${maximumCurveCount}`);
  }

  const bindings = portableMotionSourceBindings(options.sourceParameters);
  const { curves, skipped } = importCurves(value.Curves, bindings);
  const channels = normalizedPoseChannels.filter((channel) =>
    curves.some((curve) => curve.channel === channel),
  );
  const frames = sampleFrames(curves, metadata);
  const asset: PortableMotionAsset = {
    schemaVersion: portableMotionSchemaVersion,
    id: assetId,
    name: options.name?.trim() || assetId,
    durationMs: metadata.durationMs,
    fadeInMs: metadata.fadeInMs,
    fadeOutMs: metadata.fadeOutMs,
    loopable: metadata.loopable,
    restoreAtEnd: options.restoreAtEnd ?? !metadata.loopable,
    channels,
    frames,
    channelFades: importedChannelFades(curves),
    features: [],
    source: {
      format: "motion3",
      importerVersion,
      modelId: options.sourceModelId,
      name: options.sourceName,
    },
  };
  return {
    asset,
    report: {
      totalCurves: value.Curves.length,
      parameterCurves: value.Curves.filter(
        (curve) => isRecord(curve) && curve.Target === "Parameter",
      ).length,
      importedCurves: curves.length,
      importedChannels: channels,
      skippedCurves: skipped,
    },
  };
}
