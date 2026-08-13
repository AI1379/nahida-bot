import type { ModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import type { MotionValidationWarning } from "@/domain/motionPlan";
import type {
  MotionValidationContext,
  MotionValidationResult,
  MotionValidator,
} from "@/domain/motionRuntime";
import {
  createNormalizedPoseFrame,
  neutralNormalizedPose,
  normalizedPoseChannelRanges,
  normalizedPoseChannels,
  type NormalizedMotionClip,
  type NormalizedPoseChannel,
  type NormalizedPoseFrame,
} from "@/domain/normalizedPose";

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function forbiddenReason(
  context: MotionValidationContext,
): string | null {
  const blocked = context.modelProfile.forbiddenCombos.find(
    (combo) =>
      (!combo.primitive || combo.primitive === context.primitive) &&
      (!combo.expression || combo.expression === context.expression),
  );
  return blocked?.reason ?? null;
}

function addWarning(
  warnings: MotionValidationWarning[],
  warning: MotionValidationWarning,
): void {
  const duplicate = warnings.some(
    (candidate) =>
      candidate.code === warning.code && candidate.channel === warning.channel,
  );
  if (!duplicate) warnings.push(warning);
}

function normalizedFrame(
  frame: NormalizedPoseFrame,
  durationMs: number,
  warnings: MotionValidationWarning[],
): NormalizedPoseFrame {
  const atMs = Number.isFinite(frame.atMs)
    ? clamp(frame.atMs, 0, durationMs)
    : 0;
  if (atMs !== frame.atMs) {
    addWarning(warnings, {
      code: "invalid_timestamp",
      severity: "warning",
      message: "A non-finite or out-of-range keyframe timestamp was corrected.",
      atMs,
      corrected: true,
    });
  }

  const normalized = createNormalizedPoseFrame(atMs);
  for (const channel of normalizedPoseChannels) {
    const rawValue = frame[channel];
    const fallback = neutralNormalizedPose[channel];
    const finiteValue = Number.isFinite(rawValue) ? rawValue : fallback;
    const range = normalizedPoseChannelRanges[channel];
    normalized[channel] = clamp(finiteValue, range.minimum, range.maximum);
    if (normalized[channel] !== rawValue) {
      addWarning(warnings, {
        code: "channel_out_of_range",
        severity: "warning",
        message: "A canonical pose value was clamped to its allowed range.",
        channel,
        atMs,
        corrected: true,
      });
    }
  }
  return normalized;
}

function normalizedTimeline(
  clip: NormalizedMotionClip,
  warnings: MotionValidationWarning[],
): NormalizedPoseFrame[] {
  const byTimestamp = new Map<number, NormalizedPoseFrame>();
  for (const frame of clip.frames) {
    const normalized = normalizedFrame(frame, clip.durationMs, warnings);
    byTimestamp.set(normalized.atMs, normalized);
  }
  const frames = [...byTimestamp.values()].sort(
    (left, right) => left.atMs - right.atMs,
  );
  if (frames[0]?.atMs !== 0 && frames[0]) {
    frames.unshift({ ...frames[0], atMs: 0 });
    addWarning(warnings, {
      code: "missing_start_frame",
      severity: "warning",
      message: "A start keyframe was inserted at 0ms.",
      atMs: 0,
      corrected: true,
    });
  }
  if (frames.at(-1)?.atMs !== clip.durationMs && frames.at(-1)) {
    frames.push({ ...frames.at(-1)!, atMs: clip.durationMs });
    addWarning(warnings, {
      code: "missing_end_frame",
      severity: "warning",
      message: "An end keyframe was inserted at the clip duration.",
      atMs: clip.durationMs,
      corrected: true,
    });
  }
  return frames;
}

function applyIntensityScale(
  frames: NormalizedPoseFrame[],
  channels: NormalizedPoseChannel[],
  profile: ModelPerformanceProfile,
): void {
  const scale = clamp(profile.intensityScale, 0, 2);
  if (scale === 1 || !frames[0]) return;
  for (const channel of channels) {
    const base = frames[0][channel];
    const range = normalizedPoseChannelRanges[channel];
    for (const frame of frames.slice(1)) {
      frame[channel] = clamp(
        base + (frame[channel] - base) * scale,
        range.minimum,
        range.maximum,
      );
    }
  }
}

function limitChannelDynamics(
  frames: NormalizedPoseFrame[],
  channel: NormalizedPoseChannel,
  profile: ModelPerformanceProfile,
  warnings: MotionValidationWarning[],
): void {
  const maxVelocity = profile.maxVelocity[channel];
  const maxAcceleration = profile.maxAcceleration[channel];
  if ((!maxVelocity && !maxAcceleration) || frames.length < 2) return;

  let previousVelocity = 0;
  for (let index = 1; index < frames.length; index += 1) {
    const previous = frames[index - 1];
    const frame = frames[index];
    if (!previous || !frame) continue;
    const deltaSeconds = Math.max((frame.atMs - previous.atMs) / 1000, 0.001);
    const requestedVelocity = (frame[channel] - previous[channel]) / deltaSeconds;
    let velocity = maxVelocity
      ? clamp(requestedVelocity, -maxVelocity, maxVelocity)
      : requestedVelocity;
    if (maxAcceleration) {
      const velocityDelta = maxAcceleration * deltaSeconds;
      velocity = clamp(
        velocity,
        previousVelocity - velocityDelta,
        previousVelocity + velocityDelta,
      );
    }
    if (Math.abs(velocity - requestedVelocity) > 0.000_001) {
      addWarning(warnings, {
        code: "dynamics_limited",
        severity: "warning",
        message: "Velocity or acceleration was limited by the model profile.",
        channel,
        atMs: frame.atMs,
        corrected: true,
      });
      frame[channel] = previous[channel] + velocity * deltaSeconds;
    }
    previousVelocity = velocity;
  }
}

function rejectedResult(
  code: string,
  message: string,
): MotionValidationResult {
  return {
    status: "rejected",
    clip: null,
    warnings: [{ code, severity: "error", message, corrected: false }],
  };
}

export class RuleMotionValidator implements MotionValidator {
  readonly id = "rule-motion-validator";
  readonly version = "1.0.0";

  validate(
    clip: NormalizedMotionClip,
    context: MotionValidationContext,
  ): MotionValidationResult {
    if (!Number.isFinite(clip.durationMs) || clip.durationMs <= 0) {
      return rejectedResult("invalid_duration", "Motion duration must be positive.");
    }
    if (!clip.frames.length || !clip.channels.length) {
      return rejectedResult("empty_clip", "Motion clip has no active timeline.");
    }
    const reason = forbiddenReason(context);
    if (reason) return rejectedResult("forbidden_combo", reason);

    const warnings: MotionValidationWarning[] = [];
    const channels = [...new Set(clip.channels)];
    const frames = normalizedTimeline(clip, warnings);
    if (!frames.length) return rejectedResult("empty_clip", "Motion clip has no frames.");

    applyIntensityScale(frames, channels, context.modelProfile);
    for (const channel of channels) {
      limitChannelDynamics(frames, channel, context.modelProfile, warnings);
    }

    return {
      status: warnings.length ? "corrected" : "accepted",
      clip: {
        ...clip,
        channels,
        frames,
      },
      warnings,
    };
  }
}
