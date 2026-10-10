import { afterEach, expect, it, vi } from "vitest";
import { HttpWavAsrProvider, pcmWav } from "./voiceAsr";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it("uses a configurable remote endpoint and a separate ASR credential", async () => {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ text: " 你好 " }) }));
  vi.stubGlobal("fetch", fetcher);
  const provider = new HttpWavAsrProvider({ endpoint: "https://asr.example/v1/asr/transcriptions", token: "asr-key", language: "zh" });
  expect(provider.streaming).toBe(false);
  const turn = provider.openTurn();
  turn.pushAudio(new ArrayBuffer(640));
  expect(await turn.finish()).toEqual({ text: "你好", language: undefined });
  const [url, request] = fetcher.mock.calls[0] as unknown as [URL, RequestInit];
  expect(url.origin).toBe("https://asr.example");
  expect(request.headers).toMatchObject({ Authorization: "Bearer asr-key" });
  expect(request.redirect).toBe("error");
  expect((request.body as ArrayBuffer).byteLength).toBe(684);
});

it("encodes a correct WAV header and bounds per-turn buffering", () => {
  const view = new DataView(pcmWav([new ArrayBuffer(640)]));
  expect(view.getUint32(24, true)).toBe(16000);
  expect(view.getUint32(40, true)).toBe(640);
  const turn = new HttpWavAsrProvider({ endpoint: "http://localhost/asr", token: "key", language: "en" }).openTurn();
  for (let i = 0; i < 1500; i++) turn.pushAudio(new ArrayBuffer(640));
  expect(() => turn.pushAudio(new ArrayBuffer(640))).toThrow("30 秒");
  turn.cancel();
});

it("aborts an in-flight recognition request on cancellation", async () => {
  let signal: AbortSignal | undefined;
  vi.stubGlobal("fetch", vi.fn((_url, request) => {
    signal = request.signal;
    return new Promise((_resolve, reject) => signal!.addEventListener("abort", () => reject(new Error("aborted"))));
  }));
  const turn = new HttpWavAsrProvider({ endpoint: "http://localhost/asr", token: "key", language: "en" }).openTurn();
  turn.pushAudio(new ArrayBuffer(640));
  const finished = turn.finish();
  turn.cancel();
  await expect(finished).rejects.toThrow("aborted");
  expect(signal?.aborted).toBe(true);
});

it("waits for a cancelled native inference to release the worker", async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(null, { status: 429 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ text: "next utterance" }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const turn = new HttpWavAsrProvider({ endpoint: "http://localhost/asr", token: "key", language: "en" }).openTurn();
  turn.pushAudio(new ArrayBuffer(640));
  const finished = turn.finish();
  await vi.advanceTimersByTimeAsync(250);
  expect(await finished).toEqual({ text: "next utterance", language: undefined });
  expect(fetcher).toHaveBeenCalledTimes(2);
});
