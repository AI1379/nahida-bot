import { describe, expect, it, vi } from "vitest";

import { AudioPlaybackAbortedError } from "@/services/audioPlaybackAdapter";
import {
  selectSystemSpeechVoice,
  SystemSpeechAdapter,
} from "./systemSpeechAdapter";

interface FakeUtterance {
  text: string;
  rate: number;
  pitch: number;
  lang: string;
  voice: SpeechSynthesisVoice | null;
  volume: number;
  onend: SpeechSynthesisUtterance["onend"];
  onerror: SpeechSynthesisUtterance["onerror"];
}

function fakeUtterance(text: string): SpeechSynthesisUtterance {
  return {
    text,
    rate: 1,
    pitch: 1,
    lang: "",
    voice: null,
    volume: 1,
    onend: null,
    onerror: null,
  } as SpeechSynthesisUtterance;
}

describe("SystemSpeechAdapter", () => {
  const chineseFemaleVoice = {
    voiceURI: "Microsoft Xiaoxiao",
    name: "Microsoft Xiaoxiao Online",
    lang: "zh-CN",
    localService: false,
    default: false,
  } as SpeechSynthesisVoice;
  const chineseMaleVoice = {
    voiceURI: "Microsoft Yunxi",
    name: "Microsoft Yunxi Online",
    lang: "zh-CN",
    localService: false,
    default: true,
  } as SpeechSynthesisVoice;

  it("prefers a matching Chinese female voice in automatic mode", () => {
    expect(
      selectSystemSpeechVoice(
        [chineseMaleVoice, chineseFemaleVoice],
        {
          language: "zh-CN",
          voiceUri: "",
          preferFemale: true,
          rate: 1,
          pitch: 0,
          volume: 1,
        },
      ),
    ).toBe(chineseFemaleVoice);
  });

  it("uses an explicitly selected voice over automatic preferences", () => {
    expect(
      selectSystemSpeechVoice(
        [chineseFemaleVoice, chineseMaleVoice],
        {
          language: "zh-CN",
          voiceUri: chineseMaleVoice.voiceURI,
          preferFemale: true,
          rate: 1,
          pitch: 0,
          volume: 1,
        },
      ),
    ).toBe(chineseMaleVoice);
  });

  it("maps DisplayPlan speed and semitone pitch to Web Speech", async () => {
    const spoken: FakeUtterance[] = [];
    const synthesis = {
      speak: vi.fn((utterance: SpeechSynthesisUtterance) => {
        spoken.push(utterance as unknown as FakeUtterance);
      }),
      cancel: vi.fn(),
      getVoices: vi.fn(() => [chineseFemaleVoice]),
    } as unknown as SpeechSynthesis;
    const adapter = new SystemSpeechAdapter(
      synthesis,
      fakeUtterance,
      () => ({
        language: "zh-CN",
        voiceUri: "",
        preferFemale: true,
        rate: 1,
        pitch: 0,
        volume: 0.8,
      }),
    );
    const controller = new AbortController();

    const playback = adapter.play(
      {
        text: "hello",
        voice: { speed: 1.25, pitch: 6 },
      },
      controller.signal,
    );

    expect(spoken).toHaveLength(1);
    expect(spoken[0]?.rate).toBe(1.25);
    expect(spoken[0]?.pitch).toBeCloseTo(Math.SQRT2);
    expect(spoken[0]?.lang).toBe("zh-CN");
    expect(spoken[0]?.voice).toBe(chineseFemaleVoice);
    expect(spoken[0]?.volume).toBe(0.8);
    spoken[0]?.onend?.call(
      spoken[0] as unknown as SpeechSynthesisUtterance,
      {} as SpeechSynthesisEvent,
    );
    await playback;
  });

  it("cancels active system speech when aborted", async () => {
    const synthesis = {
      speak: vi.fn(),
      cancel: vi.fn(),
      getVoices: vi.fn(() => []),
    } as unknown as SpeechSynthesis;
    const adapter = new SystemSpeechAdapter(synthesis, fakeUtterance);
    const controller = new AbortController();
    const playback = adapter.play({ text: "hello" }, controller.signal);

    controller.abort();

    await expect(playback).rejects.toBeInstanceOf(AudioPlaybackAbortedError);
    expect(synthesis.cancel).toHaveBeenCalled();
  });
});
