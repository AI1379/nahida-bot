/**
 * Local pomodoro timer that fires DesktopEvent notifications when work/break
 * intervals complete. Timer state is entirely local; no Gateway round-trip.
 *
 * `settings.enabled` is the reminder master switch: while it is off no
 * `notification.reminder` events are emitted. Starting the timer itself is
 * always allowed so the UI can drive it from the actual run state instead of
 * guessing from persisted settings.
 *
 * Audio clips can be pre-synthesised and cached via the Gateway TTS adapter
 * so that voice playback on trigger is instant (zero network latency).
 */

import type { DesktopEvent } from "@/domain/runtime";
import type { PomodoroSettings } from "@/domain/config";

export type PomodoroPhase = "idle" | "working" | "breaking";

export type PomodoroReminderKind =
  | "work-start"
  | "break-start"
  | "break-end"
  | "rounds-done";

export interface PomodoroState {
  phase: PomodoroPhase;
  /** 1-based round number of the current run; 0 when idle. */
  round: number;
  /** Rounds captured from settings when the run started; 0 when idle. */
  totalRounds: number;
  startedAt: string | null;
  /** ISO timestamp when the current phase will end (null when idle). */
  expiresAt: string | null;
  remainingSeconds: number;
}

export const idlePomodoroState: PomodoroState = {
  phase: "idle",
  round: 0,
  totalRounds: 0,
  startedAt: null,
  expiresAt: null,
  remainingSeconds: 0,
};

export interface PomodoroServiceCallbacks {
  onTick: (event: DesktopEvent) => void;
  onStateChange?: (state: PomodoroState) => void;
  getSettings: () => PomodoroSettings;
  /**
   * Returns a dynamically generated reminder line when one is available
   * (Gateway model, prefetched during the phase runway); null falls back
   * to the static settings texts.
   */
  getReminderText?: (kind: PomodoroReminderKind) => string | null;
}

export class PomodoroService {
  private phase: PomodoroPhase = "idle";
  private round = 0;
  private totalRounds = 0;
  private startedAt: string | null = null;
  private expiresAt: string | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly callbacks: PomodoroServiceCallbacks;

  constructor(callbacks: PomodoroServiceCallbacks) {
    this.callbacks = callbacks;
  }

  get state(): PomodoroState {
    const now = Date.now();
    const remaining =
      this.expiresAt && this.phase !== "idle"
        ? Math.max(0, Math.round((new Date(this.expiresAt).getTime() - now) / 1000))
        : 0;
    return {
      phase: this.phase,
      round: this.round,
      totalRounds: this.totalRounds,
      startedAt: this.startedAt,
      expiresAt: this.expiresAt,
      remainingSeconds: remaining,
    };
  }

  start(): void {
    if (this.phase !== "idle") return;

    const settings = this.callbacks.getSettings();
    this.round = 0;
    // Snapshot the configured rounds so mid-run setting edits do not
    // retroactively change how long this run goes.
    this.totalRounds = settings.totalRounds;
    this.beginRound();
  }

  stop(): void {
    this.clearTimer();
    this.phase = "idle";
    this.round = 0;
    this.totalRounds = 0;
    this.startedAt = null;
    this.expiresAt = null;
    this.notifyStateChange();
  }

  toggle(): void {
    if (this.phase === "idle") {
      this.start();
    } else {
      this.stop();
    }
  }

  dispose(): void {
    this.clearTimer();
  }

  private beginRound(): void {
    const settings = this.callbacks.getSettings();
    this.round += 1;
    this.beginPhase("working", settings.workDurationMinutes);
    this.emitReminder("work-start", settings.workStartText);
  }

  private beginPhase(phase: PomodoroPhase, durationMinutes: number): void {
    this.clearTimer();
    const now = new Date();
    this.phase = phase;
    this.startedAt = now.toISOString();
    this.expiresAt = new Date(now.getTime() + durationMinutes * 60_000).toISOString();
    this.timer = setTimeout(() => this.onPhaseEnd(), durationMinutes * 60_000);
    this.notifyStateChange();
  }

  private onPhaseEnd(): void {
    const settings = this.callbacks.getSettings();

    if (this.phase === "working") {
      this.beginPhase("breaking", settings.breakDurationMinutes);
      this.emitReminder("break-start", settings.breakStartText);
    } else if (this.phase === "breaking") {
      if (this.round < this.totalRounds) {
        this.emitReminder("break-end", settings.breakEndText);
        this.beginRound();
      } else {
        this.emitReminder("rounds-done", settings.roundsDoneText);
        this.phase = "idle";
        this.round = 0;
        this.totalRounds = 0;
        this.startedAt = null;
        this.expiresAt = null;
        this.notifyStateChange();
      }
    }
  }

  private emitReminder(
    kind: PomodoroReminderKind,
    fallbackMessage: string,
  ): void {
    const settings = this.callbacks.getSettings();
    if (!settings.enabled) return;

    const generated = this.callbacks.getReminderText?.(kind);
    const message = generated?.trim() ? generated.trim() : fallbackMessage;

    this.callbacks.onTick({
      type: "notification.reminder",
      source: "local",
      at: new Date().toISOString(),
      message,
      ttsEnabled: settings.speakReminders,
      dedupeKey: `pomodoro:${kind}:${this.startedAt ?? ""}`,
    });
  }

  private notifyStateChange(): void {
    this.callbacks.onStateChange?.(this.state);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
