import { afterEach, describe, expect, it, vi } from "vitest";

import type { DisplayPlan } from "@/domain/displayPlan";
import type { PresentationPlan } from "@/domain/runtime";
import type {
  AudioPlaybackAdapter,
  AudioPlaybackRequest,
  PreloadedAudioHandle,
} from "@/services/audioPlaybackAdapter";
import { AudioPlaybackAbortedError } from "@/services/audioPlaybackAdapter";
import { SpeechPlaybackCoordinator } from "./speechPlaybackCoordinator";

class FakeAudioPlaybackAdapter implements AudioPlaybackAdapter {
  readonly requests: AudioPlaybackRequest[] = [];
  available = true;
  fail = false;
  stopCount = 0;

  isAvailable(): boolean {
    return this.available;
  }

  async play(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<void> {
    const handle = await this.fetch(request, signal);
    await handle.play(signal);
  }

  async fetch(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<PreloadedAudioHandle> {
    const self = this;
    return {
      async play(playSignal: AbortSignal): Promise<void> {
        self.requests.push(request);
        if (playSignal.aborted) throw new AudioPlaybackAbortedError();
        if (self.fail) throw new Error("playback failed");
      },
      dispose(): void {},
    };
  }

  stop(): void {
    this.stopCount += 1;
  }
}

class BlockingAudioPlaybackAdapter extends FakeAudioPlaybackAdapter {
  override async fetch(
    request: AudioPlaybackRequest,
    signal: AbortSignal,
  ): Promise<PreloadedAudioHandle> {
    const self = this;
    return {
      async play(playSignal: AbortSignal): Promise<void> {
        self.requests.push(request);
        if (request.text === "second") return;
        if (playSignal.aborted) throw new AudioPlaybackAbortedError();

        await new Promise<void>((_resolve, reject) => {
          playSignal.addEventListener(
            "abort",
            () => reject(new AudioPlaybackAbortedError()),
            { once: true },
          );
        });
      },
      dispose(): void {},
    };
  }
}

function presentation(
  id: string,
  segments: DisplayPlan["segments"],
  interruption: PresentationPlan["interruption"] = "replace",
): PresentationPlan {
  const text = segments.map((segment) => segment.text).join(" ");
  return {
    id,
    source: "local",
    displayPlan: {
      version: "1.0",
      text,
      segments,
    },
    bubbleText: text,
    ttsEnabled: segments.some((segment) => Boolean(segment.voice)),
    interruption,
    createdAt: "2026-06-12T00:00:00.000Z",
  };
}

describe("SpeechPlaybackCoordinator", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("plays voiced segments serially and reports completion", async () => {
    const adapter = new FakeAudioPlaybackAdapter();
    const starts: Array<[number, string]> = [];
    const completed: string[] = [];
    const coordinator = new SpeechPlaybackCoordinator(adapter, {
      onSegmentStart(item, index, _segment, mode) {
        starts.push([index, mode]);
        expect(item.id).toBe("voice");
      },
      onPresentationComplete(item) {
        completed.push(item.id);
      },
    });

    coordinator.play(
      presentation("voice", [
        { text: "first", voice: { speed: 1 } },
        { text: "second", voice: { style: "calm", pitch: -1 } },
      ]),
    );
    await coordinator.whenIdle();

    expect(adapter.requests.map((request) => request.text)).toEqual([
      "first",
      "second",
    ]);
    expect(starts).toEqual([
      [0, "audio"],
      [1, "audio"],
    ]);
    expect(completed).toEqual(["voice"]);
  });

  it("keeps subtitle-only segments visible for the fallback duration", async () => {
    vi.useFakeTimers();
    const adapter = new FakeAudioPlaybackAdapter();
    adapter.available = false;
    const modes: string[] = [];
    const completed: string[] = [];
    const coordinator = new SpeechPlaybackCoordinator(adapter, {
      onSegmentStart(_item, _index, _segment, mode) {
        modes.push(mode);
      },
      onPresentationComplete(item) {
        completed.push(item.id);
      },
    });

    coordinator.play(presentation("subtitle", [{ text: "short" }]));
    await vi.advanceTimersByTimeAsync(1400);
    await coordinator.whenIdle();

    expect(adapter.requests).toEqual([]);
    expect(modes).toEqual(["timed"]);
    expect(completed).toEqual(["subtitle"]);
  });

  it("falls back to timed subtitles when system playback fails", async () => {
    vi.useFakeTimers();
    const adapter = new FakeAudioPlaybackAdapter();
    adapter.fail = true;
    const fallbackIndexes: number[] = [];
    const coordinator = new SpeechPlaybackCoordinator(adapter, {
      onSegmentFallback(_item, index) {
        fallbackIndexes.push(index);
      },
    });

    coordinator.play(
      presentation("fallback", [
        { text: "failed voice", voice: { style: "soft" } },
      ]),
    );
    await vi.advanceTimersByTimeAsync(1400);
    await coordinator.whenIdle();

    expect(fallbackIndexes).toEqual([0]);
  });

  it("aborts the active presentation when a replacement arrives", async () => {
    const adapter = new BlockingAudioPlaybackAdapter();
    const completed: string[] = [];
    const coordinator = new SpeechPlaybackCoordinator(adapter, {
      onPresentationComplete(item) {
        completed.push(item.id);
      },
    });

    coordinator.play(
      presentation("first", [{ text: "first", voice: { speed: 1 } }]),
    );
    await Promise.resolve();
    coordinator.play(
      presentation("second", [{ text: "second", voice: { speed: 1 } }]),
    );
    await coordinator.whenIdle();

    expect(adapter.requests.map((request) => request.text)).toEqual([
      "first",
      "second",
    ]);
    expect(completed).toEqual(["second"]);
    expect(adapter.stopCount).toBeGreaterThan(0);
  });

  it("preserves presentation order for queued interruptions", async () => {
    const adapter = new FakeAudioPlaybackAdapter();
    const completed: string[] = [];
    const coordinator = new SpeechPlaybackCoordinator(adapter, {
      onPresentationComplete(item) {
        completed.push(item.id);
      },
    });

    coordinator.play(
      presentation(
        "first",
        [{ text: "first", voice: { speed: 1 } }],
        "queue",
      ),
    );
    coordinator.play(
      presentation(
        "second",
        [{ text: "second", voice: { speed: 1 } }],
        "queue",
      ),
    );
    await coordinator.whenIdle();

    expect(adapter.requests.map((request) => request.text)).toEqual([
      "first",
      "second",
    ]);
    expect(completed).toEqual(["first", "second"]);
  });
});
