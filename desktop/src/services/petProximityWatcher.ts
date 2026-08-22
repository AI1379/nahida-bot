import { isTauri } from "@tauri-apps/api/core";
import {
  currentMonitor,
  cursorPosition,
  getCurrentWindow,
  primaryMonitor,
} from "@tauri-apps/api/window";

import { petProximityDefaults } from "@/config/desktopRuntimeDefaults";
import {
  proximityIntent,
  type ProximityIntent,
  type ProximityThresholds,
} from "@/domain/petProximity";
import type { PetRuntimeStatus } from "@/domain/runtime";

/**
 * Watches the global cursor while the pet window is click-through.
 *
 * The OS never delivers mouse events to a click-through window, so the
 * only portable way to implement "wake on mouse near the edge" is to poll
 * the global cursor position and compare it against the visible part of
 * the pet window. Intents are throttled so the main window is not spammed.
 */
export class PetProximityWatcher {
  private timer: ReturnType<typeof setInterval> | null = null;
  private polling = false;
  private lastActivityAt = 0;

  start(
    getStatus: () => PetRuntimeStatus,
    getThresholds: () => ProximityThresholds,
    onIntent: (intent: ProximityIntent) => void,
  ): void {
    if (!isTauri() || this.timer !== null) return;
    this.timer = setInterval(() => {
      if (this.polling) return;
      this.polling = true;
      void this.poll(getStatus, getThresholds)
        .then((intent) => {
          if (intent === null) return;
          if (intent === "activity") {
            const now = Date.now();
            if (
              now - this.lastActivityAt <
              petProximityDefaults.activityThrottleMs
            ) {
              return;
            }
            this.lastActivityAt = now;
          }
          onIntent(intent);
        })
        .catch(() => {})
        .finally(() => {
          this.polling = false;
        });
    }, petProximityDefaults.pollIntervalMs);
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async poll(
    getStatus: () => PetRuntimeStatus,
    getThresholds: () => ProximityThresholds,
  ): Promise<ProximityIntent | null> {
    const status = getStatus();
    if (status === "emerging" || status === "retreating" || status === "error") {
      return null;
    }

    const appWindow = getCurrentWindow();
    const [cursor, position, size, monitor] = await Promise.all([
      cursorPosition(),
      appWindow.outerPosition(),
      appWindow.outerSize(),
      currentMonitor().then((found) => found ?? primaryMonitor()),
    ]);
    if (!monitor) return null;

    return proximityIntent(
      getStatus(),
      { x: cursor.x, y: cursor.y },
      { x: position.x, y: position.y, width: size.width, height: size.height },
      {
        x: monitor.workArea.position.x,
        y: monitor.workArea.position.y,
        width: monitor.workArea.size.width,
        height: monitor.workArea.size.height,
      },
      getThresholds(),
    );
  }
}
