import type { PortableMotionSourceParameter } from "@/domain/portableMotion";
import { defaultPoseParameterMap } from "@/domain/modelPerformanceProfile";
import {
  normalizedPoseChannelRanges,
  normalizedPoseChannels,
  type NormalizedPoseChannel,
} from "@/domain/normalizedPose";

export interface PortableMotionSourceBinding
  extends Omit<PortableMotionSourceParameter, "channel"> {
  channel: NormalizedPoseChannel | null;
}

const standardChannelByParameterId = new Map(
  normalizedPoseChannels.flatMap((channel) =>
    defaultPoseParameterMap[channel].map(
      (id) => [id.toLowerCase(), channel] as const,
    ),
  ),
);

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

export function nonEmptyPortableMotionString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value.trim();
}

function validateSourceParameter(
  parameter: PortableMotionSourceParameter,
): PortableMotionSourceBinding {
  const id = nonEmptyPortableMotionString(parameter.id, "source parameter id");
  const minimum = finiteNumber(parameter.minimum, `${id} minimum`);
  const maximum = finiteNumber(parameter.maximum, `${id} maximum`);
  const defaultValue = finiteNumber(parameter.defaultValue, `${id} default`);
  if (maximum <= minimum) throw new Error(`${id} range must be positive`);
  if (defaultValue < minimum || defaultValue > maximum) {
    throw new Error(`${id} default must stay inside its range`);
  }
  const standardChannel = standardChannelByParameterId.get(id.toLowerCase());
  return {
    id,
    minimum,
    maximum,
    defaultValue,
    channel: parameter.channel ?? standardChannel ?? null,
  };
}

/** Validate source bounds and create an exact-ID lookup for curve importers. */
export function portableMotionSourceBindings(
  parameters: PortableMotionSourceParameter[],
): Map<string, PortableMotionSourceBinding> {
  const bindings = new Map<string, PortableMotionSourceBinding>();
  for (const parameter of parameters) {
    const binding = validateSourceParameter(parameter);
    if (bindings.has(binding.id)) {
      throw new Error(`duplicate source parameter id: ${binding.id}`);
    }
    bindings.set(binding.id, binding);
  }
  return bindings;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

/** Normalize a source-model parameter value into its canonical pose channel. */
export function normalizePortableMotionSourceValue(
  channel: NormalizedPoseChannel,
  value: number,
  source: PortableMotionSourceBinding,
): number {
  const channelRange = normalizedPoseChannelRanges[channel];
  if (channelRange.minimum === 0) {
    return clamp(
      (value - source.minimum) / (source.maximum - source.minimum),
      0,
      1,
    );
  }
  const delta = value - source.defaultValue;
  const span =
    delta >= 0
      ? source.maximum - source.defaultValue
      : source.defaultValue - source.minimum;
  return span > 0 ? clamp(delta / span, -1, 1) : 0;
}

/** Convert a canonical pose value into a concrete target parameter range. */
export function denormalizePortableMotionValue(
  channel: NormalizedPoseChannel,
  value: number,
  target: Pick<
    PortableMotionSourceBinding,
    "minimum" | "maximum" | "defaultValue"
  >,
): number {
  const channelRange = normalizedPoseChannelRanges[channel];
  const normalized = clamp(value, channelRange.minimum, channelRange.maximum);
  if (channelRange.minimum === 0) {
    return target.minimum + normalized * (target.maximum - target.minimum);
  }
  return normalized >= 0
    ? target.defaultValue + normalized * (target.maximum - target.defaultValue)
    : target.defaultValue + normalized * (target.defaultValue - target.minimum);
}
