import {
  createNormalizedPoseFrame,
  normalizedPoseChannelRanges,
  type NormalizedMotionClip,
  type NormalizedPoseChannel,
  type NormalizedPoseFrame,
} from "@/domain/normalizedPose";
import type { MotionPlan } from "@/domain/motionPlan";
import { compileMotionPlan } from "./motionPlanCompiler";

export interface MotionReplayChannelMetrics {
  maximumVelocity: number;
  maximumAcceleration: number;
  maximumJerk: number;
}

export interface MotionReplayMetrics {
  durationMs: number;
  sampleRateHz: number;
  frameCount: number;
  outOfRangeCount: number;
  channels: Partial<Record<NormalizedPoseChannel, MotionReplayChannelMetrics>>;
}

export interface MotionReplayResult {
  frames: NormalizedPoseFrame[];
  metrics: MotionReplayMetrics;
}

function sampleChannel(
  clip: NormalizedMotionClip,
  channel: NormalizedPoseChannel,
  atMs: number,
): number {
  const time = clip.loopable && clip.durationMs > 0
    ? atMs % clip.durationMs
    : Math.min(atMs, clip.durationMs);
  let previous = clip.frames[0]!;
  let next = clip.frames.at(-1)!;
  for (let index = 1; index < clip.frames.length; index += 1) {
    next = clip.frames[index]!;
    if (time <= next.atMs) break;
    previous = next;
  }
  const span = next.atMs - previous.atMs;
  if (span <= 0) return next[channel];
  const progress = Math.max(0, Math.min(1, (time - previous.atMs) / span));
  return previous[channel] + (next[channel] - previous[channel]) * progress;
}

function channelMetrics(
  frames: NormalizedPoseFrame[],
  channel: NormalizedPoseChannel,
): MotionReplayChannelMetrics {
  let maximumVelocity = 0;
  let maximumAcceleration = 0;
  let maximumJerk = 0;
  let previousVelocity = 0;
  let previousAcceleration = 0;
  for (let index = 1; index < frames.length; index += 1) {
    const previous = frames[index - 1]!;
    const frame = frames[index]!;
    const seconds = Math.max((frame.atMs - previous.atMs) / 1000, 0.001);
    const velocity = (frame[channel] - previous[channel]) / seconds;
    const acceleration = (velocity - previousVelocity) / seconds;
    const jerk = (acceleration - previousAcceleration) / seconds;
    maximumVelocity = Math.max(maximumVelocity, Math.abs(velocity));
    maximumAcceleration = Math.max(maximumAcceleration, Math.abs(acceleration));
    maximumJerk = Math.max(maximumJerk, Math.abs(jerk));
    previousVelocity = velocity;
    previousAcceleration = acceleration;
  }
  return { maximumVelocity, maximumAcceleration, maximumJerk };
}

export function replayMotionClip(
  clip: NormalizedMotionClip,
  sampleRateHz = 60,
): MotionReplayResult {
  const safeRate = Math.max(1, Math.min(240, sampleRateHz));
  const frameCount = Math.max(2, Math.ceil(clip.durationMs / (1000 / safeRate)) + 1);
  const frames = Array.from({ length: frameCount }, (_, index) => {
    const atMs = index === frameCount - 1
      ? clip.durationMs
      : index * (1000 / safeRate);
    const values = Object.fromEntries(
      clip.channels.map((channel) => [channel, sampleChannel(clip, channel, atMs)]),
    );
    return createNormalizedPoseFrame(atMs, values);
  });
  let outOfRangeCount = 0;
  const channels: MotionReplayMetrics["channels"] = {};
  for (const channel of clip.channels) {
    const range = normalizedPoseChannelRanges[channel];
    outOfRangeCount += frames.filter(
      (frame) => frame[channel] < range.minimum || frame[channel] > range.maximum,
    ).length;
    channels[channel] = channelMetrics(frames, channel);
  }
  return {
    frames,
    metrics: {
      durationMs: clip.durationMs,
      sampleRateHz: safeRate,
      frameCount,
      outOfRangeCount,
      channels,
    },
  };
}

export function replayMotionPlan(
  plan: MotionPlan,
  sampleRateHz = 60,
): MotionReplayResult | null {
  const clip = compileMotionPlan(plan);
  return clip ? replayMotionClip(clip, sampleRateHz) : null;
}
