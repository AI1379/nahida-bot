import type { NormalizedPoseChannel } from "./normalizedPose";
import { normalizedPoseChannels } from "./normalizedPose";

export interface ModelPerformanceForbiddenCombo {
  expression?: string;
  primitive?: string;
  reason: string;
}

export interface ModelPerformanceProfile {
  schemaVersion: 1;
  modelId: string;
  profileVersion: string;
  poseParameterMap: Record<NormalizedPoseChannel, string[]>;
  expressionMap: Record<string, string[]>;
  motionMap: Record<string, unknown>;
  intensityScale: number;
  maxVelocity: Partial<Record<NormalizedPoseChannel, number>>;
  maxAcceleration: Partial<Record<NormalizedPoseChannel, number>>;
  preferredIdleEnergy: number;
  forbiddenCombos: ModelPerformanceForbiddenCombo[];
}

export const defaultPoseParameterMap: Record<
  NormalizedPoseChannel,
  readonly string[]
> = {
  headYaw: ["ParamAngleX", "PARAM_ANGLE_X"],
  headPitch: ["ParamAngleY", "PARAM_ANGLE_Y"],
  headRoll: ["ParamAngleZ", "PARAM_ANGLE_Z"],
  bodyYaw: ["ParamBodyAngleX", "PARAM_BODY_ANGLE_X"],
  bodyPitch: ["ParamBodyAngleY", "PARAM_BODY_ANGLE_Y"],
  bodyRoll: ["ParamBodyAngleZ", "PARAM_BODY_ANGLE_Z"],
  gazeX: ["ParamEyeBallX", "PARAM_EYE_BALL_X"],
  gazeY: ["ParamEyeBallY", "PARAM_EYE_BALL_Y"],
  browUpLeft: ["ParamBrowLY", "PARAM_BROW_L_Y"],
  browUpRight: ["ParamBrowRY", "PARAM_BROW_R_Y"],
  eyeOpenLeft: ["ParamEyeLOpen", "PARAM_EYE_L_OPEN"],
  eyeOpenRight: ["ParamEyeROpen", "PARAM_EYE_R_OPEN"],
  mouthOpen: ["ParamMouthOpenY", "PARAM_MOUTH_OPEN_Y"],
  mouthSmile: ["ParamMouthForm", "PARAM_MOUTH_FORM"],
  breath: ["ParamBreath", "PARAM_BREATH"],
  energy: [],
};

const defaultMaxVelocity: Record<NormalizedPoseChannel, number> = {
  headYaw: 4,
  headPitch: 4,
  headRoll: 4,
  bodyYaw: 2.5,
  bodyPitch: 2.5,
  bodyRoll: 2.5,
  gazeX: 8,
  gazeY: 8,
  browUpLeft: 6,
  browUpRight: 6,
  eyeOpenLeft: 12,
  eyeOpenRight: 12,
  mouthOpen: 12,
  mouthSmile: 8,
  breath: 2,
  energy: 4,
};

export function createDefaultModelPerformanceProfile(
  modelId: string,
): ModelPerformanceProfile {
  return {
    schemaVersion: 1,
    modelId,
    profileVersion: "default-v1",
    poseParameterMap: Object.fromEntries(
      Object.entries(defaultPoseParameterMap).map(([channel, ids]) => [
        channel,
        [...ids],
      ]),
    ) as Record<NormalizedPoseChannel, string[]>,
    expressionMap: {},
    motionMap: {},
    intensityScale: 1,
    maxVelocity: { ...defaultMaxVelocity },
    maxAcceleration: Object.fromEntries(
      Object.entries(defaultMaxVelocity).map(([channel, velocity]) => [
        channel,
        velocity * 6,
      ]),
    ) as Partial<Record<NormalizedPoseChannel, number>>,
    preferredIdleEnergy: 0.18,
    forbiddenCombos: [],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(
  value: unknown,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(minimum, Math.min(maximum, value))
    : fallback;
}

function sanitizeParameterMap(
  value: unknown,
  fallback: ModelPerformanceProfile["poseParameterMap"],
): ModelPerformanceProfile["poseParameterMap"] {
  const record = isRecord(value) ? value : {};
  return Object.fromEntries(
    normalizedPoseChannels.map((channel) => {
      const ids = Array.isArray(record[channel])
        ? record[channel]
            .filter((id): id is string => typeof id === "string")
            .map((id) => id.trim().slice(0, 96))
            .filter(Boolean)
            .slice(0, 16)
        : fallback[channel];
      return [channel, [...new Set(ids)]];
    }),
  ) as ModelPerformanceProfile["poseParameterMap"];
}

function sanitizeChannelLimits(
  value: unknown,
  fallback: Partial<Record<NormalizedPoseChannel, number>>,
  maximum: number,
): Partial<Record<NormalizedPoseChannel, number>> {
  const record = isRecord(value) ? value : {};
  return Object.fromEntries(
    normalizedPoseChannels.flatMap((channel) => {
      const fallbackValue = fallback[channel];
      const raw = record[channel] ?? fallbackValue;
      return typeof raw === "number" && Number.isFinite(raw) && raw > 0
        ? [[channel, Math.min(raw, maximum)]]
        : [];
    }),
  );
}

export function sanitizeModelPerformanceProfile(
  value: unknown,
  fallback: ModelPerformanceProfile,
): ModelPerformanceProfile {
  const record = isRecord(value) ? value : {};
  const forbidden = Array.isArray(record.forbiddenCombos)
    ? record.forbiddenCombos.flatMap((item) => {
        if (!isRecord(item) || typeof item.reason !== "string") return [];
        const expression = typeof item.expression === "string"
          ? item.expression.trim().slice(0, 96)
          : undefined;
        const primitive = typeof item.primitive === "string"
          ? item.primitive.trim().slice(0, 96)
          : undefined;
        const reason = item.reason.trim().slice(0, 256);
        return reason ? [{ expression, primitive, reason }] : [];
      }).slice(0, 64)
    : [...fallback.forbiddenCombos];
  return {
    schemaVersion: 1,
    modelId: fallback.modelId,
    profileVersion:
      typeof record.profileVersion === "string"
        ? record.profileVersion.trim().slice(0, 64) || fallback.profileVersion
        : fallback.profileVersion,
    poseParameterMap: sanitizeParameterMap(
      record.poseParameterMap,
      fallback.poseParameterMap,
    ),
    expressionMap: isRecord(record.expressionMap)
      ? Object.fromEntries(
          Object.entries(record.expressionMap).flatMap(([key, rawNames]) => {
            if (!Array.isArray(rawNames)) return [];
            const names = rawNames
              .filter((name): name is string => typeof name === "string")
              .map((name) => name.trim().slice(0, 160))
              .filter(Boolean);
            return [[key.trim().slice(0, 96), [...new Set(names)]]];
          }),
        )
      : { ...fallback.expressionMap },
    motionMap: isRecord(record.motionMap)
      ? { ...record.motionMap }
      : { ...fallback.motionMap },
    intensityScale: finiteNumber(record.intensityScale, fallback.intensityScale, 0, 2),
    maxVelocity: sanitizeChannelLimits(
      record.maxVelocity,
      fallback.maxVelocity,
      100,
    ),
    maxAcceleration: sanitizeChannelLimits(
      record.maxAcceleration,
      fallback.maxAcceleration,
      1000,
    ),
    preferredIdleEnergy: finiteNumber(
      record.preferredIdleEnergy,
      fallback.preferredIdleEnergy,
      0,
      1,
    ),
    forbiddenCombos: forbidden,
  };
}

function stableProfileHash(source: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

/** Assigns a deterministic version whenever a locally calibrated profile changes. */
export function withLocalModelPerformanceProfileVersion(
  profile: ModelPerformanceProfile,
): ModelPerformanceProfile {
  const fingerprint = JSON.stringify({
    poseParameterMap: normalizedPoseChannels.map((channel) => [
      channel,
      profile.poseParameterMap[channel],
    ]),
    intensityScale: profile.intensityScale,
    maxVelocity: normalizedPoseChannels.map((channel) => [
      channel,
      profile.maxVelocity[channel] ?? null,
    ]),
    maxAcceleration: normalizedPoseChannels.map((channel) => [
      channel,
      profile.maxAcceleration[channel] ?? null,
    ]),
    preferredIdleEnergy: profile.preferredIdleEnergy,
    forbiddenCombos: profile.forbiddenCombos,
  });
  return {
    ...profile,
    profileVersion: `local-${stableProfileHash(fingerprint)}`,
  };
}
