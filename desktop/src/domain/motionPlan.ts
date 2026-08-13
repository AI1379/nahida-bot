import type { MotionIntent } from "./motionIntent";
import type { NormalizedPoseFrame } from "./normalizedPose";

export type MotionValidationSeverity = "info" | "warning" | "error";

export interface MotionValidationWarning {
  code: string;
  severity: MotionValidationSeverity;
  message: string;
  channel?: string;
  atMs?: number;
  corrected: boolean;
}

export type MotionPlanSegment =
  | {
      type: "primitive";
      name: string;
      atMs: number;
      durationMs: number;
      params: Record<string, number | string | boolean>;
    }
  | {
      type: "pose-keyframes";
      atMs: number;
      durationMs: number;
      keyframes: NormalizedPoseFrame[];
    }
  | {
      type: "expression";
      atMs: number;
      expressionKey: string;
      blendMs?: number;
    };

export interface MotionPlan {
  schemaVersion: 1;
  id: string;
  intent: MotionIntent;
  createdAt: string;
  durationMs: number;
  segments: MotionPlanSegment[];
  validationWarnings: MotionValidationWarning[];
  telemetryId?: string;
}
