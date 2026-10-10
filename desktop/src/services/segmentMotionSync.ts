import { AudioPlaybackAbortedError } from "./audioPlaybackAdapter";
import { motionPlaybackTimingDefaults } from "@/config/desktopRuntimeDefaults";

/** Correlates readiness with one segment; late replies cannot start newer audio. */
export class SegmentMotionSync {
  private readonly pending = new Map<string, (ready: boolean) => void>();
  private surfaces = 0;

  get hasSurface(): boolean { return this.surfaces > 0; }

  registerSurface(): () => void {
    this.surfaces += 1;
    return () => { this.surfaces -= 1; };
  }

  acknowledge(requestId: string): void { this.pending.get(requestId)?.(true); }

  wait(requestId: string, signal: AbortSignal, timeoutMs: number = motionPlaybackTimingDefaults.readinessTimeoutMs): Promise<boolean> {
    if (signal.aborted) return Promise.reject(new AudioPlaybackAbortedError());
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", abort);
        this.pending.delete(requestId);
      };
      const finish = (ready: boolean) => {
        cleanup();
        resolve(ready);
      };
      const abort = () => { cleanup(); reject(new AudioPlaybackAbortedError()); };
      const timer = setTimeout(() => finish(false), timeoutMs);
      this.pending.set(requestId, finish);
      signal.addEventListener("abort", abort, { once: true });
    });
  }
}

export const segmentMotionSync = new SegmentMotionSync();

export function waitForSegmentStart(startedAtMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(new AudioPlaybackAbortedError());
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(new AudioPlaybackAbortedError());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, Math.max(0, startedAtMs - Date.now()));
    signal.addEventListener("abort", abort, { once: true });
  });
}
