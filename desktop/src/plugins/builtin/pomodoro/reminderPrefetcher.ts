import type { PomodoroSettings } from "@/domain/config";
import {
  gatewayWsUrlToHttpBase,
  type GatewayConnectionSettings,
} from "@/domain/gatewayConnection";
import type {
  PomodoroPhase,
  PomodoroReminderKind,
} from "@/services/pomodoroService";
import { fetchGeneratedPomodoroReminder } from "@/services/pomodoroTextService";

export interface PomodoroReminderPrefetchDependencies {
  getSettings(): PomodoroSettings;
  getGatewayConnection(): GatewayConnectionSettings;
}

interface ReminderFetchContext {
  settings: PomodoroSettings;
  httpBase: string;
  bearer: string;
  synthesize: boolean;
  controller: AbortController;
}

/** Keeps optional model-generated reminder text outside the timer lifecycle. */
export class PomodoroReminderPrefetcher {
  private readonly dependencies: PomodoroReminderPrefetchDependencies;
  private readonly generatedTexts = new Map<PomodoroReminderKind, string>();
  private readonly recentTexts: string[] = [];
  private controller: AbortController | null = null;

  constructor(dependencies: PomodoroReminderPrefetchDependencies) {
    this.dependencies = dependencies;
  }

  schedule(next: {
    phase: PomodoroPhase;
    round: number;
    totalRounds: number;
  }): void {
    if (next.phase === "working") {
      void this.prefetch(["break-start"]);
      return;
    }
    if (next.phase !== "breaking") return;
    const kinds: PomodoroReminderKind[] =
      next.round >= next.totalRounds
        ? ["rounds-done"]
        : ["break-end", "work-start"];
    void this.prefetch(kinds);
  }

  get(kind: PomodoroReminderKind): string | null {
    return this.dependencies.getSettings().dynamicText
      ? this.generatedTexts.get(kind) ?? null
      : null;
  }

  dispose(): void {
    this.controller?.abort();
  }

  private async prefetch(kinds: PomodoroReminderKind[]): Promise<void> {
    const settings = this.dependencies.getSettings();
    if (!settings.dynamicText) return;
    const connection = this.dependencies.getGatewayConnection();
    const bearer = connection.adminBearerToken;
    const httpBase = gatewayWsUrlToHttpBase(connection.gatewayWsUrl);
    if (!bearer || !httpBase) return;

    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const synthesize = connection.ttsSource !== "system";
    const fetchContext: ReminderFetchContext = {
      settings,
      httpBase,
      bearer,
      synthesize,
      controller,
    };
    await Promise.all(
      kinds.map((kind) => this.fetchOne(kind, fetchContext)),
    );
  }

  private async fetchOne(
    kind: PomodoroReminderKind,
    context: ReminderFetchContext,
  ): Promise<void> {
    const { bearer, controller, httpBase, settings, synthesize } = context;
    try {
      const reminder = await fetchGeneratedPomodoroReminder({
        httpBase,
        bearer,
        kind,
        avoid: [...this.recentTexts],
        synthesize,
        model: settings.dynamicTextModel,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      this.generatedTexts.set(kind, reminder.text);
      this.recentTexts.push(reminder.text);
      if (this.recentTexts.length > 12) this.recentTexts.shift();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      // Static plugin settings remain the failure fallback.
    }
  }
}
