import type {
  BaseMotionProfile,
  CommonLive2DParameterRole,
} from "@/domain/live2dBaseMotion";

import { clamp, lerp, smoothstep } from "./live2dMath";

export interface RuntimeParameterKeyframe {
  atMs: number;
  value: number;
}

export interface RuntimeParameterOverride {
  original: number;
  keyframes: RuntimeParameterKeyframe[];
  startedAt: number;
  durationMs: number;
}

export function groupProceduralTargets(
  profile: BaseMotionProfile,
): Map<CommonLive2DParameterRole, RuntimeParameterKeyframe[]> {
  const grouped = new Map<CommonLive2DParameterRole, RuntimeParameterKeyframe[]>();
  for (const keyframe of profile.keyframes) {
    for (const target of keyframe.targets) {
      const values = grouped.get(target.role) ?? [];
      values.push({
        atMs: keyframe.atMs,
        value: target.value,
      });
      grouped.set(target.role, values);
    }
  }
  return grouped;
}

export function createRuntimeParameterOverride(options: {
  profile: BaseMotionProfile;
  current: number;
  minimum: number;
  maximum: number;
  targets: RuntimeParameterKeyframe[];
  startedAt: number;
}): RuntimeParameterOverride {
  const { profile, current, minimum, maximum, targets, startedAt } = options;
  const keyframes = [
    { atMs: 0, value: current },
    ...targets.map((target) => ({
      atMs: target.atMs,
      value: clamp(target.value, minimum, maximum),
    })),
    { atMs: profile.durationMs, value: current },
  ].sort((left, right) => left.atMs - right.atMs);

  return {
    original: current,
    keyframes,
    startedAt,
    durationMs: profile.durationMs,
  };
}

export function runtimeParameterValueAt(
  override: RuntimeParameterOverride,
  elapsedMs: number,
): number {
  const keyframes = override.keyframes;
  if (keyframes.length === 0) return override.original;

  let previous = keyframes[0];
  let next = keyframes[keyframes.length - 1];
  for (let index = 1; index < keyframes.length; index += 1) {
    next = keyframes[index];
    if (elapsedMs <= next.atMs) break;
    previous = next;
  }

  const duration = Math.max(next.atMs - previous.atMs, 1);
  const progress = smoothstep((elapsedMs - previous.atMs) / duration);
  return lerp(previous.value, next.value, progress);
}
