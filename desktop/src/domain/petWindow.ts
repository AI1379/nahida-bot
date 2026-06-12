import type { PetWindowEdge } from "./config";
import type { PetRuntimeStatus } from "./runtime";

export interface PhysicalRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PhysicalSize {
  width: number;
  height: number;
}

export interface PhysicalPoint {
  x: number;
  y: number;
}

function visibleLength(
  status: PetRuntimeStatus,
  axisLength: number,
  exposedPx: number,
): number {
  const exposed = Math.min(axisLength, Math.max(1, exposedPx));
  if (status === "hidden" || status === "retreating") return exposed;
  if (status === "peek") {
    return Math.min(axisLength, Math.max(exposed * 3, axisLength * 0.28));
  }
  return axisLength;
}

export function petWindowPosition(
  workArea: PhysicalRect,
  windowSize: PhysicalSize,
  edge: PetWindowEdge,
  status: PetRuntimeStatus,
  exposedPx: number,
): PhysicalPoint {
  const right = workArea.x + workArea.width;
  const bottom = workArea.y + workArea.height;
  const horizontalVisible = visibleLength(
    status,
    windowSize.width,
    exposedPx,
  );
  const verticalVisible = visibleLength(
    status,
    windowSize.height,
    exposedPx,
  );

  let point: PhysicalPoint;
  switch (edge) {
    case "left":
      point = {
        x: workArea.x - windowSize.width + horizontalVisible,
        y: bottom - windowSize.height,
      };
      break;
    case "right":
      point = {
        x: right - horizontalVisible,
        y: bottom - windowSize.height,
      };
      break;
    case "top":
      point = {
        x: right - windowSize.width,
        y: workArea.y - windowSize.height + verticalVisible,
      };
      break;
    case "bottom":
      point = {
        x: right - windowSize.width,
        y: bottom - verticalVisible,
      };
      break;
  }

  return {
    x: Math.round(point.x),
    y: Math.round(point.y),
  };
}
