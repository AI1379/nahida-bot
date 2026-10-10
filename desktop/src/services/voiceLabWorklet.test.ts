import { describe, expect, it } from "vitest";
import workletSource from "./voiceLab.worklet.js?raw";

function processor(rate = 48000) {
  const messages: any[] = [];
  let Processor: any;
  new Function("sampleRate", "AudioWorkletProcessor", "registerProcessor", workletSource)(
    rate,
    class { port = { postMessage: (message: any) => messages.push(message) }; },
    (_name: string, value: any) => { Processor = value; },
  );
  return { instance: new Processor(), messages };
}

describe("Voice Lab audio worklet", () => {
  it.each([44100, 48000])("captures exact 20ms frames over five simulated minutes at %i Hz", (rate) => {
    const { instance, messages } = processor(rate);
    let remaining = rate * 300;
    while (remaining) {
      const length = Math.min(128, remaining);
      instance.process([[new Float32Array(length).fill(0.25)]], [[new Float32Array(length)]]);
      remaining -= length;
    }
    const frames = messages.filter((message) => message.type === "capture");
    expect(frames).toHaveLength(15000);
    expect(frames.every((frame) => frame.pcm.byteLength === 640)).toBe(true);
    expect(frames[0].rms).toBeCloseTo(0.25);
  });

  it("flushes queued PCM and rejects superseded commands in the audio thread", () => {
    const { instance, messages } = processor();
    instance.command({ type: "reset", epoch: 1 });
    instance.command({ type: "audio", epoch: 1, pcm: new Int16Array(480).fill(1000) });
    instance.command({ type: "reset", epoch: 2 });
    instance.command({ type: "audio", epoch: 1, pcm: new Int16Array(480).fill(1000) });
    instance.command({ type: "finish", epoch: 1 });
    const output = new Float32Array(128);
    instance.process([], [[output]]);
    expect(output.every((sample) => sample === 0)).toBe(true);
    expect(messages.some((event) => event.type === "done")).toBe(false);
  });

  it("bounds the queue and reports completion only after queued audio drains", () => {
    const { instance, messages } = processor();
    instance.command({ type: "reset", epoch: 1 });
    instance.command({ type: "audio", epoch: 1, pcm: new Int16Array(12000).fill(1000) });
    instance.command({ type: "audio", epoch: 1, pcm: new Int16Array(480) });
    expect(messages.at(-1).type).toBe("overflow");
    instance.command({ type: "finish", epoch: 1 });
    instance.process([], [[new Float32Array(23999)]]);
    expect(messages.some((event) => event.type === "done")).toBe(false);
    instance.process([], [[new Float32Array(1)]]);
    expect(messages.at(-1).type).toBe("done");
  });
});
