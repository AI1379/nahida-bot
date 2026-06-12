import type { DisplayMotion } from "./displayPlan";

export type Live2DMotionSource = "model" | "procedural" | "none";

export interface Live2DModelMotionTarget {
  source: "model";
  group: string;
  index: number;
}

export interface Live2DProceduralMotionTarget {
  source: "procedural";
  motion: DisplayMotion;
}

export interface Live2DNoneMotionTarget {
  source: "none";
}

export type Live2DMotionTarget =
  | Live2DModelMotionTarget
  | Live2DProceduralMotionTarget
  | Live2DNoneMotionTarget;

export interface Live2DMotionOption {
  source: Exclude<Live2DMotionSource, "none">;
  group: string;
  index: number;
  name: string;
  file: string;
  motion?: DisplayMotion;
}

export interface Live2DExpressionOption {
  index: number;
  name: string;
  file: string;
}

export type Live2DExpressionMap = Record<string, string[]>;

export interface Live2DModelLayout {
  scale: number;
  offsetX: number;
  offsetY: number;
  edgeExposedPx: number;
}

export interface Live2DModelManifest {
  id: string;
  name: string;
  entry: string;
  source: "bundled" | "user_import";
  emotionMap: Live2DExpressionMap;
  motionMap: Partial<Record<DisplayMotion, Live2DMotionTarget>>;
  lipSync: {
    enabled: boolean;
    parameterIds: string[];
  };
  layout: Live2DModelLayout;
}

export function live2dModelLoadKey(
  manifest: Pick<Live2DModelManifest, "id" | "entry">,
): string {
  return `${manifest.id}\u0000${manifest.entry}`;
}
