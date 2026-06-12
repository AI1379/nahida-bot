import { describe, expect, it } from "vitest";

import { sanitizeTtsSettings } from "./ttsSettingsStorage";

describe("TTS settings sanitization", () => {
  it("defaults to Chinese with female voice preference", () => {
    expect(sanitizeTtsSettings(null)).toMatchObject({
      language: "zh-CN",
      voiceUri: "",
      preferFemale: true,
      rate: 1,
      pitch: 0,
      volume: 1,
    });
  });

  it("clamps numeric settings and preserves a selected voice", () => {
    expect(
      sanitizeTtsSettings({
        language: "zh-TW",
        voiceUri: "voice-1",
        preferFemale: false,
        rate: 4,
        pitch: -20,
        volume: 2,
      }),
    ).toEqual({
      language: "zh-TW",
      voiceUri: "voice-1",
      preferFemale: false,
      rate: 1.5,
      pitch: -6,
      volume: 1,
    });
  });
});
