import { invoke, isTauri } from "@tauri-apps/api/core";
import { LogicalSize, PhysicalPosition } from "@tauri-apps/api/dpi";
import {
  currentMonitor,
  getCurrentWindow,
  primaryMonitor,
} from "@tauri-apps/api/window";

import { desktopWindowDefaults } from "@/config/desktopRuntimeDefaults";
import type { DesktopWindowState } from "@/domain/config";
import { petWindowPosition, type PhysicalPoint } from "@/domain/petWindow";
import type { PetRuntimeState } from "@/domain/runtime";

function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

function easeInQuad(t: number): number {
  return t * t;
}

export class PetWindowController {
  private initialized = false;
  private animationToken = 0;

  /**
   * Invoked when an emerging/retreating slide finishes without being
   * superseded by a newer state, so the runtime can settle the transition.
   */
  onSlideSettled: ((phase: "emerge" | "retreat") => void) | null = null;

  async apply(
    runtime: PetRuntimeState,
    windowState: DesktopWindowState,
  ): Promise<void> {
    if (!isTauri()) return;

    const appWindow = getCurrentWindow();
    const monitor = (await currentMonitor()) ?? (await primaryMonitor());
    if (!monitor) return;

    const logicalSize = new LogicalSize(windowState.width, windowState.height);
    const physicalSize = logicalSize.toPhysical(monitor.scaleFactor);
    const target = petWindowPosition(
      {
        x: monitor.workArea.position.x,
        y: monitor.workArea.position.y,
        width: monitor.workArea.size.width,
        height: monitor.workArea.size.height,
      },
      physicalSize,
      windowState.edge,
      runtime.status,
      windowState.exposedPx * monitor.scaleFactor,
    );

    const token = ++this.animationToken;
    await appWindow.setSize(logicalSize);
    await appWindow.setIgnoreCursorEvents(runtime.clickThrough);
    // Toggling click-through makes tao re-apply the latent WS_CAPTION
    // styles on Windows; strip them again before the window can be
    // activated, or a classic title bar gets painted (see lib.rs).
    await invoke("polish_pet_window").catch(() => {});

    if (runtime.status === "emerging" || runtime.status === "retreating") {
      await this.slideTo(
        appWindow,
        target,
        runtime.status === "emerging" ? "emerge" : "retreat",
        token,
      );
    } else {
      await appWindow.setPosition(new PhysicalPosition(target.x, target.y));
    }

    if (!this.initialized) {
      this.initialized = true;
      await appWindow.show();
    }

    if (runtime.interactionMode === "interactive") {
      await appWindow.setFocus();
    }
  }

  private async slideTo(
    appWindow: ReturnType<typeof getCurrentWindow>,
    target: PhysicalPoint,
    phase: "emerge" | "retreat",
    token: number,
  ): Promise<void> {
    const start = await appWindow.outerPosition();
    if (token !== this.animationToken) return;

    const durationMs = desktopWindowDefaults.slideDurationMs;
    const ease = phase === "emerge" ? easeOutCubic : easeInQuad;
    const startedAt = performance.now();

    const finished = await new Promise<boolean>((resolve) => {
      const step = () => {
        if (token !== this.animationToken) {
          resolve(false);
          return;
        }
        const progress = Math.min(
          (performance.now() - startedAt) / durationMs,
          1,
        );
        const eased = ease(progress);
        void appWindow
          .setPosition(
            new PhysicalPosition(
              Math.round(start.x + (target.x - start.x) * eased),
              Math.round(start.y + (target.y - start.y) * eased),
            ),
          )
          .catch(() => {});
        if (progress >= 1) {
          resolve(true);
          return;
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });

    if (finished) {
      this.onSlideSettled?.(phase);
    }
  }
}
