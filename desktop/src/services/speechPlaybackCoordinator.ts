import { presentationTimingDefaults } from "@/config/desktopRuntimeDefaults";
import type { DisplaySegment } from "@/domain/displayPlan";
import type { PresentationPlan } from "@/domain/runtime";
import type { AudioPlaybackAdapter } from "@/services/audioPlaybackAdapter";
import {
  AudioPlaybackAbortedError,
  isAudioPlaybackAborted,
} from "@/services/audioPlaybackAdapter";

export type SegmentPlaybackMode = "audio" | "timed";

export interface SpeechPlaybackCallbacks {
  onSegmentStart?: (
    presentation: PresentationPlan,
    index: number,
    segment: DisplaySegment,
    mode: SegmentPlaybackMode,
  ) => void;
  onSegmentFallback?: (
    presentation: PresentationPlan,
    index: number,
    segment: DisplaySegment,
    error: unknown,
  ) => void;
  onPresentationComplete?: (presentation: PresentationPlan) => void;
}

interface QueuedPresentation {
  presentation: PresentationPlan;
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

  constructor(
    adapter: AudioPlaybackAdapter,
    callbacks: SpeechPlaybackCallbacks = {},
  ) {
    this.adapter = adapter;
    this.callbacks = callbacks;
  }

  play(presentation: PresentationPlan): void {
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
      try {
        await this.playPresentation(item.presentation, controller.signal);
        if (!controller.signal.aborted) {
          this.callbacks.onPresentationComplete?.(item.presentation);
        }
      } catch (error) {
        if (!isAudioPlaybackAborted(error)) throw error;
      } finally {
        if (this.activeAbortController === controller) {
          this.activeAbortController = null;
        }
      }
    }
  }

  private async playPresentation(
    presentation: PresentationPlan,
    signal: AbortSignal,
  ): Promise<void> {
    for (
      let index = 0;
      index < presentation.displayPlan.segments.length;
      index += 1
    ) {
      const segment = presentation.displayPlan.segments[index];
      if (!segment) continue;

      const shouldPlayAudio =
        presentation.ttsEnabled &&
        Boolean(segment.voice) &&
        this.adapter.isAvailable();
      this.callbacks.onSegmentStart?.(
        presentation,
        index,
        segment,
        shouldPlayAudio ? "audio" : "timed",
      );

      if (shouldPlayAudio) {
        try {
          await this.adapter.play(
            {
              text: segment.text,
              voice: segment.voice,
            },
            signal,
          );
        } catch (error) {
          if (isAudioPlaybackAborted(error)) throw error;
          this.callbacks.onSegmentFallback?.(
            presentation,
            index,
            segment,
            error,
          );
          await abortableDelay(estimatedSegmentDuration(segment), signal);
        }
      } else {
        await abortableDelay(estimatedSegmentDuration(segment), signal);
      }

      await abortableDelay(segment.pauseAfterMs ?? 0, signal);
    }
  }

  private abortActive(): void {
    this.activeAbortController?.abort();
    this.activeAbortController = null;
    this.adapter.stop();
  }
}
