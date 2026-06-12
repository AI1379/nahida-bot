import type { PhysicalPoint, PhysicalRect } from "./petWindow";
import type { PetRuntimeStatus } from "./runtime";

export type ProximityIntent = "peek" | "emerge" | "hide" | "activity";

export interface ProximityThresholds {
  /** Cursor distance (physical px) that wakes the hidden pet into peek. */
  wakeDistancePx: number;
  /** Cursor distance (physical px) beyond which a peeking pet hides again. */
  hideDistancePx: number;
}

export function intersectRects(
  a: PhysicalRect,
  b: PhysicalRect,
): PhysicalRect | null {
  const x = Math.max(a.x, b.x);
  const y = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.width, b.x + b.width);
  const bottom = Math.min(a.y + a.height, b.y + b.height);
  if (right <= x || bottom <= y) return null;
  return { x, y, width: right - x, height: bottom - y };
}

export function distanceToRect(
  point: PhysicalPoint,
  rect: PhysicalRect,
): number {
  const dx = Math.max(rect.x - point.x, 0, point.x - (rect.x + rect.width));
  const dy = Math.max(rect.y - point.y, 0, point.y - (rect.y + rect.height));
  return Math.hypot(dx, dy);
}

/**
 * Decide what a global cursor sample means for the pet runtime.
 *
 * The pet window is click-through most of the time, so the WebView never
 * receives mouse events; callers poll the OS cursor position instead and
 * measure it against the on-screen part of the window (the strip that is
 * not hanging off the work area edge).
 */
export function proximityIntent(
  status: PetRuntimeStatus,
  cursor: PhysicalPoint,
  windowRect: PhysicalRect,
  workArea: PhysicalRect,
  thresholds: ProximityThresholds,
): ProximityIntent | null {
  const visibleRect = intersectRects(windowRect, workArea);
  if (!visibleRect) return null;

  const distance = distanceToRect(cursor, visibleRect);

  switch (status) {
    case "hidden":
      return distance <= thresholds.wakeDistancePx ? "peek" : null;
    case "peek":
      if (distance === 0) return "emerge";
      return distance > thresholds.hideDistancePx ? "hide" : null;
    case "emerged":
    case "speaking":
    case "chat":
      return distance === 0 ? "activity" : null;
    default:
      // emerging / retreating / error: transitions own the window.
      return null;
  }
}
