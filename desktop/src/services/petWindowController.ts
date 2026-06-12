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

interface PetWindowUpdate {
  runtime: PetRuntimeState;
  windowState: DesktopWindowState;
}

function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

function easeInQuad(t: number): number {
  return t * t;
}

export class PetWindowController {
  private initialized = false;
  private animationToken = 0;
  private pendingUpdate: PetWindowUpdate | null = null;
  private applyingUpdate = false;
  private disposed = false;

  /**
   * Invoked when an emerging/retreating slide finishes without being
   * superseded by a newer state, so the runtime can settle the transition.
   */
  onSlideSettled: ((phase: "emerge" | "retreat") => void) | null = null;

  schedule(
    runtime: PetRuntimeState,
    windowState: DesktopWindowState,
  ): void {
    if (this.disposed) return;
    this.pendingUpdate = {
      runtime: { ...runtime },
      windowState: { ...windowState },
    };
    this.cancelCurrentUpdate();
    if (!this.applyingUpdate) {
      void this.drainUpdates();
    }
  }

  dispose(): void {
    this.disposed = true;
    this.pendingUpdate = null;
    this.cancelCurrentUpdate();
  }

  private cancelCurrentUpdate(): void {
    this.animationToken += 1;
  }

  private async drainUpdates(): Promise<void> {
    this.applyingUpdate = true;
    try {
      while (!this.disposed && this.pendingUpdate) {
        const update = this.pendingUpdate;
        this.pendingUpdate = null;
        await this.apply(update.runtime, update.windowState);
      }
    } catch (error) {
      console.error("Failed to update the pet window", error);
    } finally {
      this.applyingUpdate = false;
      if (!this.disposed && this.pendingUpdate) {
        void this.drainUpdates();
      }
    }
  }

  private async apply(
    runtime: PetRuntimeState,
    windowState: DesktopWindowState,
  ): Promise<void> {
    if (!isTauri()) return;
    const token = ++this.animationToken;

    const appWindow = getCurrentWindow();
    const monitor = (await currentMonitor()) ?? (await primaryMonitor());
    if (!monitor || token !== this.animationToken) return;

    const logicalSize = new LogicalSize(windowState.width, windowState.height);
    const physicalSize = logicalSize.toPhysical(monitor.scaleFactor);
    const workArea = {
      x: monitor.workArea.position.x,
      y: monitor.workArea.position.y,
      width: monitor.workArea.size.width,
      height: monitor.workArea.size.height,
    };
    const exposedPx = windowState.exposedPx * monitor.scaleFactor;
    const target = petWindowPosition(
      workArea,
      physicalSize,
      windowState.edge,
      runtime.status,
      exposedPx,
    );

    await appWindow.setSize(logicalSize);
    if (token !== this.animationToken) return;
    await appWindow.setIgnoreCursorEvents(runtime.clickThrough);
    if (token !== this.animationToken) return;
    // Toggling click-through makes tao re-apply the latent WS_CAPTION
    // styles on Windows; strip them again before the window can be
    // activated, or a classic title bar gets painted (see lib.rs).
    await invoke("polish_pet_window").catch(() => {});
    if (token !== this.animationToken) return;

    if (!this.initialized) {
      const initialTarget =
        runtime.status === "emerging"
          ? petWindowPosition(
              workArea,
              physicalSize,
              windowState.edge,
              "hidden",
              exposedPx,
            )
          : target;
      await appWindow.setPosition(
        new PhysicalPosition(initialTarget.x, initialTarget.y),
      );
      if (token !== this.animationToken) return;
      await appWindow.show();
      if (token !== this.animationToken) return;
      this.initialized = true;
    }

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
    if (token !== this.animationToken) return;

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

    while (token === this.animationToken) {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (token !== this.animationToken) return;

      const progress = Math.min(
        (performance.now() - startedAt) / durationMs,
        1,
      );
      const eased = ease(progress);
      await appWindow.setPosition(
        new PhysicalPosition(
          Math.round(start.x + (target.x - start.x) * eased),
          Math.round(start.y + (target.y - start.y) * eased),
        ),
      );
      if (token !== this.animationToken) return;
      if (progress >= 1) break;
    }

    this.onSlideSettled?.(phase);
  }
}
