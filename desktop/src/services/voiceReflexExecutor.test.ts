import { describe, expect, it } from "vitest";

import type {
  LocalReflexClipPlayer,
  ReflexCancelCommand,
  ReflexPlayCommand,
  VoiceReflexCue,
} from "./voiceReflexExecutor";
import { VoiceReflexExecutor } from "./voiceReflexExecutor";

class FakeReflexPlayer implements LocalReflexClipPlayer {
  ready = true;
  blocking = false;
  readonly played: VoiceReflexCue[] = [];
  stopCount = 0;

  isReady(_cue: VoiceReflexCue): boolean {
    return this.ready;
  }

  async play(cue: VoiceReflexCue, signal: AbortSignal): Promise<void> {
    this.played.push(cue);
    if (!this.blocking) return;
    await new Promise<void>((resolve) => {
      signal.addEventListener("abort", () => resolve(), { once: true });
    });
  }

  stop(): void {
    this.stopCount += 1;
  }
}

function playCommand(overrides: Partial<ReflexPlayCommand> = {}): ReflexPlayCommand {
  return {
    type: "play",
    command_id: "command-1",
    session_id: "voice-1",
    turn_id: "turn-1",
    cue: "thinking",
    expires_at_ms: 2_000,
    interruptible: true,
    ...overrides,
  };
}

function cancelCommand(
  overrides: Partial<ReflexCancelCommand> = {},
): ReflexCancelCommand {
  return {
    type: "cancel",
    command_id: "command-1",
    session_id: "voice-1",
    turn_id: "turn-1",
    reason: "formal_audio",
    ...overrides,
  };
}

describe("VoiceReflexExecutor", () => {
  it("plays a fresh server-authorized cue from local assets", async () => {
    const player = new FakeReflexPlayer();
    const started: string[] = [];
    const completed: string[] = [];
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {
        onCueStart: (command) => started.push(command.command_id),
        onCueComplete: (command) => completed.push(command.command_id),
      },
      () => 1_000,
    );

    expect(executor.handleCommand(playCommand())).toBe(true);
    await Promise.resolve();

    expect(player.played).toEqual(["thinking"]);
    expect(started).toEqual(["command-1"]);
    expect(completed).toEqual(["command-1"]);
  });

  it("drops expired, unavailable, and duplicate play commands", async () => {
    const player = new FakeReflexPlayer();
    const dropped: string[] = [];
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      { onCommandDropped: (_command, reason) => dropped.push(reason) },
      () => 2_000,
    );

    expect(executor.handleCommand(playCommand())).toBe(false);
    player.ready = false;
    expect(
      executor.handleCommand(
        playCommand({ command_id: "command-2", expires_at_ms: 3_000 }),
      ),
    ).toBe(false);
    player.ready = true;
    const fresh = playCommand({ command_id: "command-3", expires_at_ms: 3_000 });
    expect(executor.handleCommand(fresh)).toBe(true);
    await Promise.resolve();
    expect(executor.handleCommand(fresh)).toBe(false);

    expect(dropped).toEqual(["expired", "not_preloaded", "duplicate"]);
  });

  it("applies a server cancel to active local playback", async () => {
    const player = new FakeReflexPlayer();
    player.blocking = true;
    const cancelled: string[] = [];
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {
        onCueCancelled: (_command, reason) => cancelled.push(reason),
      },
      () => 1_000,
    );

    expect(executor.handleCommand(playCommand())).toBe(true);
    expect(executor.handleCommand(cancelCommand())).toBe(true);
    await Promise.resolve();

    expect(player.stopCount).toBe(1);
    expect(cancelled).toEqual(["formal_audio"]);
  });

  it("stops immediately on local barge-in and tombstones late play", async () => {
    const player = new FakeReflexPlayer();
    player.blocking = true;
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {},
      () => 1_000,
    );

    expect(executor.handleCommand(playCommand())).toBe(true);
    expect(executor.interruptLocally("turn-1")).toBe(true);
    expect(
      executor.handleCommand(
        playCommand({ command_id: "late-command", expires_at_ms: 3_000 }),
      ),
    ).toBe(false);
    await Promise.resolve();

    expect(player.stopCount).toBe(1);
    expect(player.played).toEqual(["thinking"]);
  });

  it("ignores a stale cancel that does not own active playback", async () => {
    const player = new FakeReflexPlayer();
    player.blocking = true;
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {},
      () => 1_000,
    );
    expect(
      executor.handleCommand(
        playCommand({ command_id: "command-2", turn_id: "turn-2" }),
      ),
    ).toBe(true);

    expect(executor.handleCommand(cancelCommand())).toBe(false);
    expect(player.stopCount).toBe(0);
    executor.dispose();
    await Promise.resolve();
  });

  it("requires a cancel command to match both command and turn", async () => {
    const player = new FakeReflexPlayer();
    player.blocking = true;
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {},
      () => 1_000,
    );
    expect(executor.handleCommand(playCommand())).toBe(true);

    expect(
      executor.handleCommand(cancelCommand({ turn_id: "other-turn" })),
    ).toBe(false);
    expect(player.stopCount).toBe(0);
    executor.dispose();
    await Promise.resolve();
  });

  it("does not start playback when the start callback cancels the cue", async () => {
    const player = new FakeReflexPlayer();
    const started: string[] = [];
    let executor!: VoiceReflexExecutor;
    executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {
        onCueStart: (command) => {
          started.push(command.command_id);
          executor.interruptLocally("turn-1");
        },
      },
      () => 1_000,
    );

    expect(executor.handleCommand(playCommand())).toBe(true);
    await Promise.resolve();

    expect(started).toEqual(["command-1"]);
    expect(player.played).toEqual([]);
  });

  it("drops commands routed from another voice session", () => {
    const player = new FakeReflexPlayer();
    const dropped: string[] = [];
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      { onCommandDropped: (_command, reason) => dropped.push(reason) },
      () => 1_000,
    );

    expect(
      executor.handleCommand(
        playCommand({ session_id: "voice-2", expires_at_ms: 3_000 }),
      ),
    ).toBe(false);
    expect(
      executor.handleCommand(cancelCommand({ session_id: "voice-2" })),
    ).toBe(false);
    expect(player.played).toEqual([]);
    expect(dropped).toEqual(["wrong_session"]);
  });

  it("isolates telemetry callback failures from command handling", () => {
    const player = new FakeReflexPlayer();
    const errors: string[] = [];
    const executor = new VoiceReflexExecutor(
      "voice-1",
      player,
      {
        onCommandDropped: () => {
          throw new Error("telemetry failed");
        },
        onCueError: (_command, error) => errors.push(String(error)),
      },
      () => 2_000,
    );

    expect(() => executor.handleCommand(playCommand())).not.toThrow();
    expect(errors).toEqual(["Error: telemetry failed"]);
  });
});
