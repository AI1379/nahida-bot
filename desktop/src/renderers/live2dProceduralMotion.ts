import type {
  NormalizedMotionClip,
  NormalizedPoseChannel,
} from "@/domain/normalizedPose";

import { lerp, smoothstep } from "./live2dMath";
import {
  normalizedPoseValueToLive2D,
  type Live2DParameterRange,
} from "./live2dRetargeting";

export interface RuntimeParameterKeyframe {
  atMs: number;
  value: number;
}

export interface RuntimeParameterOverride {
  original: number;
  finalValue: number;
  keyframes: RuntimeParameterKeyframe[];
  startedAt: number;
  durationMs: number;
  loopable: boolean;
}

export function groupNormalizedClipFrames(
  clip: NormalizedMotionClip,
): Map<NormalizedPoseChannel, RuntimeParameterKeyframe[]> {
  return new Map(
    clip.channels.map((channel) => [
      channel,
      clip.frames.map((frame) => ({
        atMs: frame.atMs,
        value: frame[channel],
      })),
    ]),
  );
}

export function createRuntimeParameterOverride(options: {
  clip: NormalizedMotionClip;
  channel: NormalizedPoseChannel;
  current: number;
  original?: number;
  range: Live2DParameterRange;
  startedAt: number;
}): RuntimeParameterOverride {
  const { clip, channel, current, range, startedAt } = options;
  const original = options.original ?? current;
  const retargetedFrames = clip.frames
    .filter(
      (frame) =>
        frame.atMs > 0 &&
        (clip.restoreAtEnd
          ? frame.atMs < clip.durationMs
          : frame.atMs <= clip.durationMs),
    )
    .map((frame) => ({
      atMs: frame.atMs,
      value: normalizedPoseValueToLive2D(channel, frame[channel], range),
    }));
  const finalValue = clip.restoreAtEnd
    ? original
    : (retargetedFrames.at(-1)?.value ?? current);
  const keyframes = [
    { atMs: 0, value: current },
    ...retargetedFrames,
  ].sort((left, right) => left.atMs - right.atMs);

  if (keyframes.at(-1)?.atMs !== clip.durationMs) {
    keyframes.push({ atMs: clip.durationMs, value: finalValue });
  }

  return {
    original,
    finalValue,
    keyframes,
    startedAt,
    durationMs: clip.durationMs,
    loopable: clip.loopable,
  };
}

export function runtimeParameterValueAt(
  override: RuntimeParameterOverride,
  elapsedMs: number,
): number {
  const keyframes = override.keyframes;
  if (keyframes.length === 0) return override.original;
  const playbackElapsedMs =
    override.loopable && override.durationMs > 0
      ? elapsedMs % override.durationMs
      : elapsedMs;

  let previous = keyframes[0];
  let next = keyframes[keyframes.length - 1];
  for (let index = 1; index < keyframes.length; index += 1) {
    next = keyframes[index];
    if (playbackElapsedMs <= next.atMs) break;
    previous = next;
  }

  const duration = Math.max(next.atMs - previous.atMs, 1);
  const progress = smoothstep(
    (playbackElapsedMs - previous.atMs) / duration,
  );
  return lerp(previous.value, next.value, progress);
}
