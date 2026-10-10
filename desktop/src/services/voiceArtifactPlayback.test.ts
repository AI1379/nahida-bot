import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceArtifactPlayback } from "./voiceArtifactPlayback";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const clip = { duration: 1, sampleRate: 48000, numberOfChannels: 1, length: 48000,
  getChannelData: () => new Float32Array(48000).fill(0.25) } as unknown as AudioBuffer;

it("discards an old artifact when decoding finishes after interruption", async () => {
  let decoded!: (value: AudioBuffer) => void;
  const push = vi.fn(), finish = vi.fn();
  const player = new VoiceArtifactPlayback({ decode: () => new Promise((resolve) => { decoded = resolve; }), push, finish });
  const playing = player.play(1, new ArrayBuffer(1));
  player.cancel(); decoded(clip); await playing;
  expect(push).not.toHaveBeenCalled();
  expect(finish).not.toHaveBeenCalled();
});

it("paces complete artifacts and stops feeding PCM immediately on cancellation", async () => {
  const push = vi.fn(), finish = vi.fn();
  const player = new VoiceArtifactPlayback({ decode: async () => clip, push, finish });
  await player.play(1, new ArrayBuffer(1));
  expect(push).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(100);
  expect(push).toHaveBeenCalledTimes(6);
  player.cancel(); await vi.advanceTimersByTimeAsync(2000);
  expect(push).toHaveBeenCalledTimes(6);
  expect(finish).not.toHaveBeenCalled();
});

it("sends a finish marker only after the final chunk is queued", async () => {
  const push = vi.fn(), finish = vi.fn();
  const player = new VoiceArtifactPlayback({ decode: async () => clip, push, finish });
  await player.play(7, new ArrayBuffer(1));
  await vi.advanceTimersByTimeAsync(980);
  expect(push).toHaveBeenCalledTimes(50);
  expect(finish).toHaveBeenCalledWith(7);
});
