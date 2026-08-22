import { presentationTimingDefaults } from "@/config/desktopRuntimeDefaults";
import type { DisplaySegment } from "@/domain/displayPlan";
import type { PresentationPlan } from "@/domain/runtime";
import type {
  AudioPlaybackAdapter,
  PreloadedAudioHandle,
} from "@/services/audioPlaybackAdapter";
import {
  AudioPlaybackAbortedError,
  isAudioPlaybackAborted,
} from "@/services/audioPlaybackAdapter";

export type SegmentPlaybackMode = "audio" | "timed";

export interface SpeechPlaybackCallbacks {
  onPresentationStart?: (
    presentation: PresentationPlan,
  ) => void | Promise<void>;
  onSegmentStart?: (
    presentation: PresentationPlan,
    index: number,
    segment: DisplaySegment,
    mode: SegmentPlaybackMode,
    durationMs: number,
  ) => void | Promise<void>;
  onSegmentFallback?: (
    presentation: PresentationPlan,
    index: number,
    segment: DisplaySegment,
    error: unknown,
  ) => void;
  onPresentationComplete?: (presentation: PresentationPlan) => void;
  onPresentationInterrupted?: (presentation: PresentationPlan) => void;
  onPresentationError?: (
    presentation: PresentationPlan,
    error: unknown,
  ) => void;
}

interface QueuedPresentation {
  presentation: PresentationPlan;
}

interface SegmentAudioPreparation {
  handle: PreloadedAudioHandle | null;
  error?: unknown;
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(new AudioPlaybackAbortedError());
  if (milliseconds <= 0) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    const abort = () => {
      clearTimeout(timer);
      reject(new AudioPlaybackAbortedError());
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

function estimatedSegmentDuration(segment: DisplaySegment): number {
  const speed = segment.voice?.speed ?? 1;
  return Math.max(
    presentationTimingDefaults.minimumSegmentDurationMs,
    (segment.text.length *
      presentationTimingDefaults.millisecondsPerCharacter) /
      speed,
  );
}

export class SpeechPlaybackCoordinator {
  private readonly adapter: AudioPlaybackAdapter;
  private readonly callbacks: SpeechPlaybackCallbacks;
  private readonly queue: QueuedPresentation[] = [];
  private activeAbortController: AbortController | null = null;
  private drainTask: Promise<void> | null = null;
  private activeDedupeKey: string | null = null;

  constructor(
    adapter: AudioPlaybackAdapter,
    callbacks: SpeechPlaybackCallbacks = {},
  ) {
    this.adapter = adapter;
    this.callbacks = callbacks;
  }

  play(presentation: PresentationPlan): void {
    if (presentation.dedupeKey && this.hasDuplicate(presentation.dedupeKey)) {
      return;
    }
    if (presentation.interruption === "replace") {
      this.queue.length = 0;
      this.abortActive();
    }
    this.queue.push({ presentation });
    this.ensureDrain();
  }

  stop(): void {
    this.queue.length = 0;
    this.abortActive();
  }

  dispose(): void {
    this.stop();
  }

  async whenIdle(): Promise<void> {
    while (this.drainTask) {
      await this.drainTask;
    }
  }

  private ensureDrain(): void {
    if (this.drainTask) return;
    this.drainTask = this.drain().finally(() => {
      this.drainTask = null;
      if (this.queue.length) this.ensureDrain();
    });
  }

  private async drain(): Promise<void> {
    while (this.queue.length) {
      const item = this.queue.shift();
      if (!item) return;

      const controller = new AbortController();
      this.activeAbortController = controller;
      this.activeDedupeKey = item.presentation.dedupeKey ?? null;
      try {
        await this.playPresentation(item.presentation, controller.signal);
        if (!controller.signal.aborted) {
          this.callbacks.onPresentationComplete?.(item.presentation);
        }
      } catch (error) {
        if (isAudioPlaybackAborted(error)) {
          this.callbacks.onPresentationInterrupted?.(item.presentation);
        } else {
          this.callbacks.onPresentationError?.(item.presentation, error);
        }
      } finally {
        this.activeDedupeKey = null;
        if (this.activeAbortController === controller) {
          this.activeAbortController = null;
        }
      }
    }
  }

  private hasDuplicate(dedupeKey: string): boolean {
    if (this.activeDedupeKey === dedupeKey) return true;
    return this.queue.some(
      (item) => item.presentation.dedupeKey === dedupeKey,
    );
  }

  private async playPresentation(
    presentation: PresentationPlan,
    signal: AbortSignal,
  ): Promise<void> {
    if (signal.aborted) return;
    await this.callbacks.onPresentationStart?.(presentation);
    const segments = presentation.displayPlan.segments;
    let pendingPreparation: Promise<SegmentAudioPreparation> | null =
      segments.length
        ? this.prepareSegment(presentation, segments[0]!, signal)
        : null;

    try {
      for (let index = 0; index < segments.length; index += 1) {
        const segment = segments[index];
        if (!segment || !pendingPreparation) continue;
        const preparation = await pendingPreparation;
        const handle = preparation.handle;
        const durationMs =
          handle?.durationMs ?? estimatedSegmentDuration(segment);
        const nextSegment = segments[index + 1];
        pendingPreparation = nextSegment
          ? this.prepareSegment(presentation, nextSegment, signal)
          : null;
        await this.playPreparedSegment(
          presentation,
          index,
          segment,
          preparation,
          durationMs,
          signal,
        );
      }
    } finally {
      if (pendingPreparation) {
        try {
          (await pendingPreparation).handle?.dispose();
        } catch {
          // An aborted in-flight fetch has no handle to release.
        }
      }
    }
  }

  private async playPreparedSegment(
    presentation: PresentationPlan,
    index: number,
    segment: DisplaySegment,
    preparation: SegmentAudioPreparation,
    durationMs: number,
    signal: AbortSignal,
  ): Promise<void> {
    const handle = preparation.handle;
    if (preparation.error) {
      this.callbacks.onSegmentFallback?.(
        presentation,
        index,
        segment,
        preparation.error,
      );
    }
    await this.callbacks.onSegmentStart?.(
      presentation,
      index,
      segment,
      handle ? "audio" : "timed",
      durationMs,
    );
    if (handle) {
      await this.playAudioHandle(presentation, index, segment, handle, signal);
    } else {
      await abortableDelay(durationMs, signal);
    }
    await abortableDelay(segment.pauseAfterMs ?? 0, signal);
  }

  private async playAudioHandle(
    presentation: PresentationPlan,
    index: number,
    segment: DisplaySegment,
    handle: PreloadedAudioHandle,
    signal: AbortSignal,
  ): Promise<void> {
    try {
      await handle.play(signal);
    } catch (error) {
      if (isAudioPlaybackAborted(error)) throw error;
      this.callbacks.onSegmentFallback?.(
        presentation,
        index,
        segment,
        error,
      );
      await abortableDelay(estimatedSegmentDuration(segment), signal);
    } finally {
      handle.dispose();
    }
  }

  private async prepareSegment(
    presentation: PresentationPlan,
    segment: DisplaySegment,
    signal: AbortSignal,
  ): Promise<SegmentAudioPreparation> {
    if (
      !presentation.ttsEnabled ||
      !segment.voice ||
      !this.adapter.isAvailable()
    ) {
      return { handle: null };
    }
    try {
      return {
        handle: await this.adapter.fetch(
          { text: segment.text, voice: segment.voice },
          signal,
        ),
      };
    } catch (error) {
      if (isAudioPlaybackAborted(error)) throw error;
      return { handle: null, error };
    }
  }

  private abortActive(): void {
    this.activeAbortController?.abort();
    this.activeAbortController = null;
    this.adapter.stop();
  }
}
