import { afterEach, expect, it, vi } from "vitest";
import { VoiceLabAudio } from "./voiceLabAudio";

afterEach(() => vi.unstubAllGlobals());

it("stops a microphone granted after the user already ended the session", async () => {
  let grant!: (stream: MediaStream) => void;
  const permission = new Promise<MediaStream>((resolve) => { grant = resolve; });
  const stop = vi.fn();
  const close = vi.fn(async () => {});
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: () => permission } });
  vi.stubGlobal("AudioContext", class {
    state = "running";
    resume = async () => {};
    close = close;
  });
  const audio = new VoiceLabAudio();
  const opening = audio.open(() => {});
  await Promise.resolve();
  audio.close();
  grant({ getTracks: () => [{ stop }] } as unknown as MediaStream);
  await expect(opening).rejects.toThrow("已结束");
  expect(stop).toHaveBeenCalledOnce();
  expect(close).toHaveBeenCalledOnce();
});
