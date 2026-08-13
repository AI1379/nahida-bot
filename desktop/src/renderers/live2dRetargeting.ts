import {
  normalizedPoseChannelRanges,
  type NormalizedPoseChannel,
} from "@/domain/normalizedPose";
import { defaultPoseParameterMap } from "@/domain/modelPerformanceProfile";

export interface Live2DParameterRange {
  minimum: number;
  maximum: number;
  defaultValue: number;
}

export const live2dParameterIdsByPoseChannel = defaultPoseParameterMap;

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

export function normalizedPoseValueToLive2D(
  channel: NormalizedPoseChannel,
  value: number,
  range: Live2DParameterRange,
): number {
  const channelRange = normalizedPoseChannelRanges[channel];
  const normalized = clamp(value, channelRange.minimum, channelRange.maximum);
  if (channelRange.minimum === 0) {
    return range.minimum + normalized * (range.maximum - range.minimum);
  }
  return normalized >= 0
    ? range.defaultValue + normalized * (range.maximum - range.defaultValue)
    : range.defaultValue + normalized * (range.defaultValue - range.minimum);
}

export function live2DValueToNormalizedPose(
  channel: NormalizedPoseChannel,
  value: number,
  range: Live2DParameterRange,
): number {
  const channelRange = normalizedPoseChannelRanges[channel];
  if (channelRange.minimum === 0) {
    const span = range.maximum - range.minimum;
    return span > 0 ? clamp((value - range.minimum) / span, 0, 1) : 0;
  }
  const delta = value - range.defaultValue;
  const span = delta >= 0
    ? range.maximum - range.defaultValue
    : range.defaultValue - range.minimum;
  return span > 0 ? clamp(delta / span, -1, 1) : 0;
}
