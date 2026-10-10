import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { LabAudioEvent } from "./voiceLabAudio";

const audioMock = vi.hoisted(() => ({
  emit: null as ((event: LabAudioEvent) => void) | null,
  reset: vi.fn(), push: vi.fn(), finish: vi.fn(), close: vi.fn(),
}));
vi.mock("./voiceLabAudio", () => ({
  VoiceLabAudio: class {
    async open(callback: (event: LabAudioEvent) => void) { audioMock.emit = callback; }
    reset = audioMock.reset;
    push = audioMock.push;
    finish = audioMock.finish;
    close = audioMock.close;
    reflexPlayer() { return { isReady: () => true, play: async () => {}, stop: () => {} }; }
  },
}));
import { VoiceLabClient, type VoiceLabStats } from "./voiceLabClient";

class FakeSocket {
  static OPEN = 1;
  static latest: FakeSocket;
  readyState = 1;
  bufferedAmount = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | ArrayBuffer }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  readonly url: URL;
  constructor(url: URL) { this.url = url; FakeSocket.latest = this; }
  event(data: object) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal("WebSocket", FakeSocket); });
afterEach(() => vi.unstubAllGlobals());

async function connected(asr?: import("./voiceAsr").VoiceAsrProvider) {
  const updates: VoiceLabStats[] = [];
  const client = new VoiceLabClient({
    gatewayUrl: "ws://localhost:6185/api/nodes/ws", token: "test-token",
    asr,
    settings: () => ({ threshold: 0.03, delayMs: 600, injectLate: true }),
    onStats: (value) => updates.push(value),
  });
  await client.start();
  const socket = FakeSocket.latest;
  socket.onopen?.();
  socket.event({ type: "ready", session_id: "lab-session", mode: asr ? "external_asr" : "mock", tts_mode: asr ? "artifact" : "mock" });
  return { client, socket, updates };
}

it("authenticates outside the URL and stops playback before server acknowledgement", async () => {
  const { client, socket } = await connected();
  expect(socket.url.href).toBe("ws://localhost:6185/api/voice/lab");
  expect(JSON.parse(socket.send.mock.calls[0]![0])).toMatchObject({ token: "test-token", mode: "mock" });
  client.simulateTurn();
  expect(audioMock.reset).toHaveBeenLastCalledWith(1);
  socket.event({ type: "listening", epoch: 1, turn_id: "turn-1" });
  client.simulateTurn();
  expect(audioMock.reset).toHaveBeenLastCalledWith(2);
  socket.event({ type: "output_done", epoch: 1 });
  expect(audioMock.finish).not.toHaveBeenCalled();
  client.close();
  expect(audioMock.close).toHaveBeenCalledOnce();
});

it("releases audio on disconnection and ignores callbacks after closing", async () => {
  const { client, socket, updates } = await connected();
  socket.onclose?.({ code: 1006 });
  expect(audioMock.close).toHaveBeenCalledOnce();
  expect(updates.at(-1)?.active).toBe(false);
  expect(updates.at(-1)?.error).toContain("1006");
  const count = socket.send.mock.calls.length;
  audioMock.emit?.({ type: "capture", epoch: 0, pcm: new ArrayBuffer(640), rms: 0.1 });
  expect(socket.send).toHaveBeenCalledTimes(count);
  client.close();
  expect(audioMock.close).toHaveBeenCalledOnce();
});

it("terminates a session with growing upload backpressure", async () => {
  const { socket, updates } = await connected();
  socket.bufferedAmount = 32001;
  audioMock.emit?.({ type: "capture", epoch: 0, pcm: new ArrayBuffer(640), rms: 0 });
  expect(audioMock.close).toHaveBeenCalledOnce();
  expect(updates.at(-1)?.error).toContain("上传积压");
});

it("keeps microphone PCM out of Gateway and rejects late ASR text after barge-in", async () => {
  let resolve!: (value: { text: string }) => void;
  const first = { pushAudio: vi.fn(), finish: () => new Promise<{ text: string }>((done) => { resolve = done; }), cancel: vi.fn() };
  const second = { pushAudio: vi.fn(), finish: async () => ({ text: "new" }), cancel: vi.fn() };
  const openTurn = vi.fn().mockReturnValueOnce(first).mockReturnValue(second);
  const { client, socket } = await connected({ streaming: false, openTurn });
  const frame = (rms: number) => audioMock.emit?.({ type: "capture", epoch: 0, pcm: new ArrayBuffer(640), rms });
  for (let i = 0; i < 3; i++) frame(0.1);
  for (let i = 0; i < 23; i++) frame(0);
  for (let i = 0; i < 3; i++) frame(0.1);
  expect(first.cancel).toHaveBeenCalled();
  resolve({ text: "stale" }); await Promise.resolve();
  expect(socket.send.mock.calls.every(([value]) => typeof value === "string")).toBe(true);
  expect(socket.send.mock.calls.some(([value]) => JSON.parse(value).type === "transcript_final")).toBe(false);
  client.close();
});
