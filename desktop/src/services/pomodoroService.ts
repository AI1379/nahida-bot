/**
 * Local pomodoro timer that fires DesktopEvent notifications when work/break
 * intervals complete. Timer state is entirely local; no Gateway round-trip.
 *
 * Audio clips can be pre-synthesised and cached via the Gateway TTS adapter
 * so that voice playback on trigger is instant (zero network latency).
 */

import type { DesktopEvent } from "@/domain/runtime";
import type { PomodoroSettings } from "@/domain/config";

export type PomodoroPhase = "idle" | "working" | "breaking";

export interface PomodoroState {
  phase: PomodoroPhase;
  startedAt: string | null;
  /** ISO timestamp when the current phase will end (null when idle). */
  expiresAt: string | null;
  remainingSeconds: number;
}

export interface PomodoroServiceCallbacks {
  onTick: (event: DesktopEvent) => void;
  getSettings: () => PomodoroSettings;
}

export class PomodoroService {
  private phase: PomodoroPhase = "idle";
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
      startedAt: this.startedAt,
      expiresAt: this.expiresAt,
      remainingSeconds: remaining,
    };
  }

  start(): void {
    const settings = this.callbacks.getSettings();
    if (!settings.enabled) return;
    if (this.phase !== "idle") return;

    this.beginPhase("working", settings.workDurationMinutes);
    this.emitReminder(settings.workStartText);
  }

  stop(): void {
    this.clearTimer();
    this.phase = "idle";
    this.startedAt = null;
    this.expiresAt = null;
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

  private beginPhase(phase: PomodoroPhase, durationMinutes: number): void {
    this.clearTimer();
    const now = new Date();
    this.phase = phase;
    this.startedAt = now.toISOString();
    this.expiresAt = new Date(now.getTime() + durationMinutes * 60_000).toISOString();
    this.timer = setTimeout(() => this.onPhaseEnd(), durationMinutes * 60_000);
  }

  private onPhaseEnd(): void {
    const settings = this.callbacks.getSettings();

    if (this.phase === "working") {
      this.emitReminder(settings.breakStartText);
      this.beginPhase("breaking", settings.breakDurationMinutes);
    } else if (this.phase === "breaking") {
      this.emitReminder(settings.breakEndText);
      this.phase = "idle";
      this.startedAt = null;
      this.expiresAt = null;
    }
  }

  private emitReminder(message: string): void {
    const settings = this.callbacks.getSettings();
    if (!settings.enabled) return;

    this.callbacks.onTick({
      type: "notification.reminder",
      source: "local",
      at: new Date().toISOString(),
      message,
      dedupeKey: `pomodoro:${this.phase}:${this.startedAt ?? ""}`,
    });
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
