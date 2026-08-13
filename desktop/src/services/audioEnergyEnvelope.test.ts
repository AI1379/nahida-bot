import { describe, expect, it } from "vitest";

import { audioRmsEnergy, smoothAudioEnergy } from "./audioEnergyEnvelope";

describe("audio energy envelope", () => {
  it("removes the configured noise floor and normalizes RMS energy", () => {
    expect(audioRmsEnergy(new Uint8Array([128, 128, 128, 128]))).toBe(0);
    expect(audioRmsEnergy(new Uint8Array([0, 255, 0, 255]))).toBe(1);
    expect(audioRmsEnergy(new Uint8Array([112, 144, 112, 144]))).toBeGreaterThan(
      0,
    );
  });

  it("uses a faster attack than release", () => {
    const attacked = smoothAudioEnergy(0, 1, 50);
    const released = smoothAudioEnergy(1, 0, 50);

    expect(attacked).toBeGreaterThan(1 - released);
    expect(attacked).toBeGreaterThan(0);
    expect(released).toBeLessThan(1);
  });
});
