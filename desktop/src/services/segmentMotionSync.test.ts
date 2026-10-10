import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentMotionSync, waitForSegmentStart } from "./segmentMotionSync";
import { AudioPlaybackAbortedError } from "./audioPlaybackAdapter";

afterEach(() => vi.useRealTimers());

describe("SegmentMotionSync", () => {
  it("only accepts readiness for the requested segment", async () => {
    const sync = new SegmentMotionSync();
    const wait = sync.wait("current", new AbortController().signal);
    let ready = false;
    void wait.then(() => { ready = true; });
    sync.acknowledge("previous");
    await Promise.resolve();
    expect(ready).toBe(false);
    sync.acknowledge("current");
    expect(await wait).toBe(true);
  });

  it("degrades on timeout and cancels readiness on interruption", async () => {
    vi.useFakeTimers();
    const sync = new SegmentMotionSync();
    const timeout = sync.wait("missing", new AbortController().signal, 100);
    await vi.advanceTimersByTimeAsync(100);
    expect(await timeout).toBe(false);
    const abort = new AbortController();
    const cancelled = expect(sync.wait("cancelled", abort.signal)).rejects.toBeInstanceOf(AudioPlaybackAbortedError);
    abort.abort();
    await cancelled;
    sync.acknowledge("cancelled");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("waits for the shared start time and supports cancellation", async () => {
    vi.useFakeTimers();
    let started = false;
    const start = waitForSegmentStart(Date.now() + 50, new AbortController().signal)
      .then(() => { started = true; });
    await vi.advanceTimersByTimeAsync(49);
    expect(started).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await start;
    expect(started).toBe(true);
    const abort = new AbortController();
    const cancelled = expect(waitForSegmentStart(Date.now() + 50, abort.signal))
      .rejects.toBeInstanceOf(AudioPlaybackAbortedError);
    abort.abort();
    await cancelled;
  });
});
