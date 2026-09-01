import type { PortableMotionSourceParameter } from "@/domain/portableMotion";

function parameter(
  id: string,
  minimum: number,
  defaultValue: number,
  maximum: number,
): PortableMotionSourceParameter {
  return { id, minimum, defaultValue, maximum };
}

/**
 * Cubism 2-style IDs using the ranges in Live2D's standard parameter list.
 * Actual model ranges should override these assumptions whenever available.
 */
export const cubism2StandardSourceParameters: PortableMotionSourceParameter[] = [
  parameter("PARAM_ANGLE_X", -30, 0, 30),
  parameter("PARAM_ANGLE_Y", -30, 0, 30),
  parameter("PARAM_ANGLE_Z", -30, 0, 30),
  parameter("PARAM_BODY_ANGLE_X", -10, 0, 10),
  parameter("PARAM_BODY_ANGLE_Y", -10, 0, 10),
  parameter("PARAM_BODY_ANGLE_Z", -10, 0, 10),
  parameter("PARAM_EYE_BALL_X", -1, 0, 1),
  parameter("PARAM_EYE_BALL_Y", -1, 0, 1),
  parameter("PARAM_BROW_L_Y", -1, 0, 1),
  parameter("PARAM_BROW_R_Y", -1, 0, 1),
  parameter("PARAM_EYE_L_OPEN", 0, 1, 1),
  parameter("PARAM_EYE_R_OPEN", 0, 1, 1),
  parameter("PARAM_MOUTH_OPEN_Y", 0, 0, 1),
  parameter("PARAM_MOUTH_FORM", -1, 0, 1),
  parameter("PARAM_BREATH", 0, 0, 1),
];

/** Cubism 3/4 standard IDs with the same documented parameter ranges. */
export const cubism3StandardTargetParameters: PortableMotionSourceParameter[] = [
  parameter("ParamAngleX", -30, 0, 30),
  parameter("ParamAngleY", -30, 0, 30),
  parameter("ParamAngleZ", -30, 0, 30),
  parameter("ParamBodyAngleX", -10, 0, 10),
  parameter("ParamBodyAngleY", -10, 0, 10),
  parameter("ParamBodyAngleZ", -10, 0, 10),
  parameter("ParamEyeBallX", -1, 0, 1),
  parameter("ParamEyeBallY", -1, 0, 1),
  parameter("ParamBrowLY", -1, 0, 1),
  parameter("ParamBrowRY", -1, 0, 1),
  parameter("ParamEyeLOpen", 0, 1, 1),
  parameter("ParamEyeROpen", 0, 1, 1),
  parameter("ParamMouthOpenY", 0, 0, 1),
  parameter("ParamMouthForm", -1, 0, 1),
  parameter("ParamBreath", 0, 0, 1),
];
