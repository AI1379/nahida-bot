import { describe, expect, it } from "vitest";
import { VoiceLabPacketGate, VoiceLabVad } from "./voiceLabProtocol";

function packet(epoch: number, sequence = 0, turn = "turn-1"): ArrayBuffer {
  const header = new TextEncoder().encode(JSON.stringify({ epoch, sequence, turn_id: turn, sample_rate_hz: 24000 }));
  const bytes = new Uint8Array(4 + header.length + 960);
  new DataView(bytes.buffer).setUint32(0, header.length, true);
  bytes.set(header, 4);
  return bytes.buffer;
}

describe("Voice Lab packet gate", () => {
  it("invalidates in-flight old audio before the server acknowledges an interrupt", () => {
    const gate = new VoiceLabPacketGate();
    gate.advance(); gate.turnId = "turn-1";
    expect(gate.accept(packet(1))).toHaveLength(480);
    gate.advance();
    expect(gate.accept(packet(1, 1))).toBeNull();
    expect(gate.accept(packet(2, 0, "turn-2"))).toBeNull();
    gate.turnId = "turn-2";
    expect(gate.accept(packet(2, 0, "turn-2"))).toHaveLength(480);
    expect(gate.accept(packet(2, 0, "turn-2"))).toBeNull();
    expect(gate.dropped).toBe(3);
  });

  it("rejects malformed and oversized headers", () => {
    const gate = new VoiceLabPacketGate();
    expect(() => gate.accept(new ArrayBuffer(2))).toThrow();
    const data = packet(1);
    new DataView(data).setUint32(0, 99999, true);
    expect(() => gate.accept(data)).toThrow();
  });
});

it("VAD ignores brief clicks and tolerates pauses under 450ms", () => {
  const vad = new VoiceLabVad();
  expect(vad.update(0.1, 0.03)).toBeNull();
  expect(vad.update(0, 0.03)).toBeNull();
  vad.update(0.1, 0.03); vad.update(0.1, 0.03);
  expect(vad.update(0.1, 0.03)).toBe("start");
  for (let i = 0; i < 22; i++) expect(vad.update(0, 0.03)).toBeNull();
  expect(vad.update(0, 0.03)).toBe("stop");
});
