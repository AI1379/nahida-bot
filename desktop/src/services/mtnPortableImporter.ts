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

import { cubism2StandardSourceParameters } from "./cubismStandardSourceParameters";
import {
  nonEmptyPortableMotionString,
  normalizePortableMotionSourceValue,
  portableMotionSourceBindings,
  type PortableMotionSourceBinding,
} from "./portableMotionSource";

const importerVersion = "mtn-portable-v1";
const maximumTextLength = 16 * 1024 * 1024;
const maximumLineCount = 20_000;
const maximumParameterCount = 4_096;
const maximumSampleCount = 1_000_000;
const maximumDurationMs = 300_000;
const maximumFps = 120;

export interface MtnPortableImportOptions {
  assetId: string;
  name?: string;
  sourceModelId?: string;
  sourceName?: string;
  sourceParameters?: PortableMotionSourceParameter[];
  useStandardParameterRanges?: boolean;
  loopable?: boolean;
  restoreAtEnd?: boolean;
}

export type MtnSkippedParameterReason =
  | "unknown_parameter"
  | "unmapped_parameter"
  | "duplicate_channel";

export interface MtnSkippedParameter {
  id: string;
  reason: MtnSkippedParameterReason;
}

export interface MtnParameterFade {
  id: string;
  fadeInMs?: number;
  fadeOutMs?: number;
}

export interface MtnClampedParameter {
  id: string;
  sampleCount: number;
}

/** Loss and source-format metadata retained for compatibility UI. */
export interface MtnPortableImportReport {
  fps: number;
  frameCount: number;
  durationMs: number;
  fadeInMs?: number;
  fadeOutMs?: number;
  parameterImportHint?: number;
  totalParameters: number;
  importedParameters: number;
  importedChannels: NormalizedPoseChannel[];
  assumedRangeParameterIds: string[];
  parameterFades: MtnParameterFade[];
  clampedParameters: MtnClampedParameter[];
  skippedParameters: MtnSkippedParameter[];
  unsupportedDirectives: string[];
}

export interface MtnPortableImportResult {
  asset: PortableMotionAsset;
  report: MtnPortableImportReport;
}

interface ParsedMtnParameter {
  id: string;
  values: number[];
}

interface ParsedMtn {
  fps: number;
  fadeInMs?: number;
  fadeOutMs?: number;
  parameterImportHint?: number;
  parameters: ParsedMtnParameter[];
  parameterFades: MtnParameterFade[];
  unsupportedDirectives: string[];
}

interface ImportedMtnParameter extends ParsedMtnParameter {
  channel: NormalizedPoseChannel;
  source: PortableMotionSourceBinding;
}

function finiteNumber(value: string, label: string): number {
  const parsed = Number(value.trim());
  if (!Number.isFinite(parsed)) throw new Error(`${label} must be a finite number`);
  return parsed;
}

function nonNegativeNumber(value: string, label: string): number {
  const parsed = finiteNumber(value, label);
  if (parsed < 0) throw new Error(`${label} must not be negative`);
  return parsed;
}

function fadeMilliseconds(value: string, label: string): number {
  const parsed = nonNegativeNumber(value, label);
  if (parsed > maximumDurationMs) throw new Error(`${label} exceeds import limit`);
  return parsed;
}

function parseDirective(
  line: string,
  state: Omit<ParsedMtn, "parameters">,
): void {
  const match = /^\$([^:=]+)(?::([^=]+))?=(.*)$/.exec(line);
  if (!match) {
    state.unsupportedDirectives.push(line);
    return;
  }
  const [, key, parameterId, rawValue] = match;
  if (key === "fps" && !parameterId) {
    state.fps = finiteNumber(rawValue, "mtn fps");
    return;
  }
  if ((key === "fadein" || key === "fadeout") && !parameterId) {
    const value = fadeMilliseconds(rawValue, `mtn ${key}`);
    if (key === "fadein") state.fadeInMs = value;
    else state.fadeOutMs = value;
    return;
  }
  if ((key === "fadein" || key === "fadeout") && parameterId) {
    let fade = state.parameterFades.find((candidate) => candidate.id === parameterId);
    if (!fade) {
      fade = { id: parameterId };
      state.parameterFades.push(fade);
    }
    const value = fadeMilliseconds(rawValue, `${key}:${parameterId}`);
    if (key === "fadein") fade.fadeInMs = value;
    else fade.fadeOutMs = value;
    return;
  }
  state.unsupportedDirectives.push(line);
}

function parseParameterLine(line: string): ParsedMtnParameter {
  const separator = line.indexOf("=");
  if (separator <= 0) throw new Error(`invalid mtn parameter line: ${line}`);
  const id = nonEmptyPortableMotionString(line.slice(0, separator), "mtn parameter id");
  const rawValues = line.slice(separator + 1).split(",");
  if (!rawValues.length || rawValues.some((value) => !value.trim())) {
    throw new Error(`${id} must contain numeric samples`);
  }
  return {
    id,
    values: rawValues.map((value, index) =>
      finiteNumber(value, `${id} sample ${index}`),
    ),
  };
}

function parseMtn(text: string): ParsedMtn {
  if (text.length > maximumTextLength) throw new Error("mtn text exceeds import limit");
  const lines = text.split(/\r?\n/);
  if (lines.length > maximumLineCount) throw new Error("mtn line count exceeds import limit");
  const state: ParsedMtn = {
    fps: 0,
    parameters: [],
    parameterFades: [],
    unsupportedDirectives: [],
  };
  const ids = new Set<string>();
  let sampleCount = 0;
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("$")) {
      parseDirective(line, state);
      continue;
    }
    const parameter = parseParameterLine(line);
    if (parameter.id === "PARAM_IMPORT") {
      state.parameterImportHint = parameter.values[0];
      continue;
    }
    if (ids.has(parameter.id)) throw new Error(`duplicate mtn parameter: ${parameter.id}`);
    ids.add(parameter.id);
    sampleCount += parameter.values.length;
    if (state.parameters.length >= maximumParameterCount) {
      throw new Error("mtn parameter count exceeds import limit");
    }
    if (sampleCount > maximumSampleCount) {
      throw new Error("mtn sample data exceeds import limit");
    }
    state.parameters.push(parameter);
  }
  if (state.fps < 1 || state.fps > maximumFps) {
    throw new Error(`mtn fps must be between 1 and ${maximumFps}`);
  }
  if (!state.parameters.length) throw new Error("mtn contains no parameters");
  return state;
}

function mergedSourceParameters(options: MtnPortableImportOptions): {
  parameters: PortableMotionSourceParameter[];
  assumedIds: Set<string>;
} {
  const useStandard = options.useStandardParameterRanges ?? true;
  const standard = useStandard ? cubism2StandardSourceParameters : [];
  const merged = new Map(standard.map((parameter) => [parameter.id, parameter]));
  const assumedIds = new Set(merged.keys());
  for (const parameter of options.sourceParameters ?? []) {
    merged.set(parameter.id, parameter);
    assumedIds.delete(parameter.id);
  }
  return { parameters: [...merged.values()], assumedIds };
}

function frameCountFor(parameters: ParsedMtnParameter[]): number {
  const frameCount = Math.max(...parameters.map((parameter) => parameter.values.length));
  for (const parameter of parameters) {
    if (parameter.values.length !== 1 && parameter.values.length !== frameCount) {
      throw new Error(
        `${parameter.id} has ${parameter.values.length} samples; expected 1 or ${frameCount}`,
      );
    }
  }
  return frameCount;
}

function importMtnParameters(
  parameters: ParsedMtnParameter[],
  bindings: Map<string, PortableMotionSourceBinding>,
): { imported: ImportedMtnParameter[]; skipped: MtnSkippedParameter[] } {
  const imported: ImportedMtnParameter[] = [];
  const skipped: MtnSkippedParameter[] = [];
  const channels = new Set<NormalizedPoseChannel>();
  for (const parameter of parameters) {
    const binding = bindings.get(parameter.id);
    let reason: MtnSkippedParameterReason | null = null;
    if (!binding) reason = "unknown_parameter";
    else if (!binding.channel) reason = "unmapped_parameter";
    else if (channels.has(binding.channel)) reason = "duplicate_channel";
    if (reason) {
      skipped.push({ id: parameter.id, reason });
      continue;
    }
    if (!binding?.channel) continue;
    imported.push({ ...parameter, channel: binding.channel, source: binding });
    channels.add(binding.channel);
  }
  return { imported, skipped };
}

function sampleAt(parameter: ImportedMtnParameter, frameIndex: number): number {
  return parameter.values.length === 1
    ? parameter.values[0]
    : parameter.values[frameIndex];
}

function portableFrames(
  imported: ImportedMtnParameter[],
  frameCount: number,
  fps: number,
): PortableMotionPoseFrame[] {
  return Array.from({ length: frameCount }, (_, frameIndex) => {
    const frame: PortableMotionPoseFrame = { atMs: (frameIndex * 1000) / fps };
    for (const parameter of imported) {
      frame[parameter.channel] = normalizePortableMotionSourceValue(
        parameter.channel,
        sampleAt(parameter, frameIndex),
        parameter.source,
      );
    }
    return frame;
  });
}

function clampedParameters(imported: ImportedMtnParameter[]): MtnClampedParameter[] {
  return imported.flatMap((parameter) => {
    const sampleCount = parameter.values.filter(
      (value) => value < parameter.source.minimum || value > parameter.source.maximum,
    ).length;
    return sampleCount ? [{ id: parameter.id, sampleCount }] : [];
  });
}

function portableChannelFades(
  imported: ImportedMtnParameter[],
  parameterFades: MtnParameterFade[],
): PortableMotionChannelFade[] {
  const fades = new Map(parameterFades.map((fade) => [fade.id, fade]));
  return imported.flatMap((parameter) => {
    const fade = fades.get(parameter.id);
    return fade
      ? [{
          channel: parameter.channel,
          fadeInMs: fade.fadeInMs,
          fadeOutMs: fade.fadeOutMs,
        }]
      : [];
  });
}

/** Parse Cubism 2 runtime motion samples into a portable normalized asset. */
export function importMtnAsPortableAsset(
  text: string,
  options: MtnPortableImportOptions,
): MtnPortableImportResult {
  const assetId = nonEmptyPortableMotionString(options.assetId, "asset id");
  const parsed = parseMtn(text);
  const frameCount = frameCountFor(parsed.parameters);
  const durationMs = Math.max(((frameCount - 1) * 1000) / parsed.fps, 1000 / parsed.fps);
  if (durationMs > maximumDurationMs) throw new Error("mtn duration exceeds import limit");
  const source = mergedSourceParameters(options);
  const bindings = portableMotionSourceBindings(source.parameters);
  const { imported, skipped } = importMtnParameters(parsed.parameters, bindings);
  const channels = normalizedPoseChannels.filter((channel) =>
    imported.some((parameter) => parameter.channel === channel),
  );
  const loopable = options.loopable ?? false;
  const asset: PortableMotionAsset = {
    schemaVersion: portableMotionSchemaVersion,
    id: assetId,
    name: options.name?.trim() || assetId,
    durationMs,
    fadeInMs: parsed.fadeInMs,
    fadeOutMs: parsed.fadeOutMs,
    loopable,
    restoreAtEnd: options.restoreAtEnd ?? !loopable,
    channels,
    frames: portableFrames(imported, frameCount, parsed.fps),
    channelFades: portableChannelFades(imported, parsed.parameterFades),
    features: [],
    source: {
      format: "mtn",
      importerVersion,
      modelId: options.sourceModelId,
      name: options.sourceName,
    },
  };
  return {
    asset,
    report: {
      fps: parsed.fps,
      frameCount,
      durationMs,
      fadeInMs: parsed.fadeInMs,
      fadeOutMs: parsed.fadeOutMs,
      parameterImportHint: parsed.parameterImportHint,
      totalParameters: parsed.parameters.length,
      importedParameters: imported.length,
      importedChannels: channels,
      assumedRangeParameterIds: imported
        .map((parameter) => parameter.id)
        .filter((id) => source.assumedIds.has(id)),
      parameterFades: parsed.parameterFades,
      clampedParameters: clampedParameters(imported),
      skippedParameters: skipped,
      unsupportedDirectives: parsed.unsupportedDirectives,
    },
  };
}
