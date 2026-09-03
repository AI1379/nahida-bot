/**
 * Desktop execution edge for server-owned realtime voice reflex commands.
 *
 * Scheduling, cooldown, cue selection, and turn ownership live in the Gateway.
 * This class only resolves preloaded local audio, drops stale commands, and
 * performs immediate playback cancellation when local VAD detects barge-in.
 */

export type VoiceReflexCue = "acknowledge" | "thinking" | "checking";

export interface ReflexPlayCommand {
  type: "play";
  command_id: string;
  session_id: string;
  turn_id: string;
  cue: VoiceReflexCue;
  expires_at_ms: number;
  interruptible: boolean;
}

export interface ReflexCancelCommand {
  type: "cancel";
  command_id: string;
  session_id: string;
  turn_id: string;
  reason: string;
}

export type ReflexCommand = ReflexPlayCommand | ReflexCancelCommand;

export type ReflexDropReason =
  | "disposed"
  | "duplicate"
  | "expired"
  | "wrong_session"
  | "turn_cancelled"
  | "not_preloaded";

export interface LocalReflexClipPlayer {
  isReady(cue: VoiceReflexCue): boolean;
  play(cue: VoiceReflexCue, signal: AbortSignal): Promise<void>;
  stop(): void;
}

export interface VoiceReflexExecutorCallbacks {
  onCueStart?: (command: ReflexPlayCommand) => void;
  onCueComplete?: (command: ReflexPlayCommand) => void;
  onCueCancelled?: (command: ReflexPlayCommand, reason: string) => void;
  onCommandDropped?: (command: ReflexPlayCommand, reason: ReflexDropReason) => void;
  onCueError?: (command: ReflexPlayCommand, error: unknown) => void;
}

interface ActiveCue {
  command: ReflexPlayCommand;
  controller: AbortController;
}

const maximumRememberedCommands = 256;
const maximumCancelledTurns = 128;

export class VoiceReflexExecutor {
  private readonly sessionId: string;
  private readonly player: LocalReflexClipPlayer;
  private readonly callbacks: VoiceReflexExecutorCallbacks;
  private readonly now: () => number;
  private readonly seenPlayCommands = new Set<string>();
  private readonly cancelledTurns = new Set<string>();
  private active: ActiveCue | null = null;
  private disposed = false;

  constructor(
    sessionId: string,
    player: LocalReflexClipPlayer,
    callbacks: VoiceReflexExecutorCallbacks = {},
    now: () => number = () => Date.now(),
  ) {
    const cleanSessionId = sessionId.trim();
    if (!cleanSessionId) throw new Error("sessionId must not be empty");
    this.sessionId = cleanSessionId;
    this.player = player;
    this.callbacks = callbacks;
    this.now = now;
  }

  /** Execute one server command. Returns whether it changed local playback. */
  handleCommand(command: ReflexCommand): boolean {
    if (command.type === "cancel") return this.handleCancel(command);
    return this.handlePlay(command);
  }

  /**
   * Stop reflex audio before a server round trip when local VAD sees speech.
   * The voice session transport must still report the interruption upstream.
   */
  interruptLocally(turnId: string, reason = "local_user_speech"): boolean {
    this.remember(this.cancelledTurns, turnId, maximumCancelledTurns);
    if (this.active?.command.turn_id !== turnId) return false;
    this.stopActive(reason);
    return true;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stopActive("disposed");
    this.seenPlayCommands.clear();
    this.cancelledTurns.clear();
  }

  private handlePlay(command: ReflexPlayCommand): boolean {
    const dropReason = this.dropReason(command);
    if (dropReason) {
      this.reportDropped(command, dropReason);
      return false;
    }
    this.remember(
      this.seenPlayCommands,
      command.command_id,
      maximumRememberedCommands,
    );
    this.stopActive("superseded");
    const controller = new AbortController();
    this.active = { command, controller };
    void this.play(command, controller);
    return true;
  }

  private handleCancel(command: ReflexCancelCommand): boolean {
    if (command.session_id !== this.sessionId) return false;
    this.remember(
      this.cancelledTurns,
      command.turn_id,
      maximumCancelledTurns,
    );
    const active = this.active;
    if (
      active?.command.command_id !== command.command_id ||
      active?.command.turn_id !== command.turn_id
    ) {
      return false;
    }
    this.stopActive(command.reason);
    return true;
  }

  private dropReason(command: ReflexPlayCommand): ReflexDropReason | null {
    if (this.disposed) return "disposed";
    if (command.session_id !== this.sessionId) return "wrong_session";
    if (this.seenPlayCommands.has(command.command_id)) return "duplicate";
    if (this.now() >= command.expires_at_ms) return "expired";
    if (this.cancelledTurns.has(command.turn_id)) return "turn_cancelled";
    if (!this.player.isReady(command.cue)) return "not_preloaded";
    return null;
  }

  private async play(
    command: ReflexPlayCommand,
    controller: AbortController,
  ): Promise<void> {
    try {
      if (controller.signal.aborted || this.disposed) return;
      this.callbacks.onCueStart?.(command);
      if (controller.signal.aborted || this.disposed) return;
      await this.player.play(command.cue, controller.signal);
      if (!controller.signal.aborted) this.callbacks.onCueComplete?.(command);
    } catch (error) {
      if (!controller.signal.aborted) this.reportError(command, error);
    } finally {
      if (this.active?.controller === controller) this.active = null;
    }
  }

  private stopActive(reason: string): void {
    const active = this.active;
    this.active = null;
    if (!active) return;
    active.controller.abort(reason);
    try {
      this.player.stop();
    } catch (error) {
      this.reportError(active.command, error);
    }
    try {
      this.callbacks.onCueCancelled?.(active.command, reason);
    } catch (error) {
      this.reportError(active.command, error);
    }
  }

  private reportDropped(
    command: ReflexPlayCommand,
    reason: ReflexDropReason,
  ): void {
    try {
      this.callbacks.onCommandDropped?.(command, reason);
    } catch (error) {
      this.reportError(command, error);
    }
  }

  private reportError(command: ReflexPlayCommand, error: unknown): void {
    try {
      this.callbacks.onCueError?.(command, error);
    } catch {
      // Telemetry callbacks must never break the realtime playback edge.
    }
  }

  private remember(
    values: Set<string>,
    value: string,
    maximumSize: number,
  ): void {
    values.add(value);
    while (values.size > maximumSize) {
      const oldest = values.values().next().value;
      if (oldest === undefined) return;
      values.delete(oldest);
    }
  }
}
