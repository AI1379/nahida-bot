/**
 * Client for the Gateway dynamic pomodoro reminder endpoint
 * (POST /api/pomodoro/reminders). Called during the phase runway so both
 * the generated line and its speech artifact are ready before the phase
 * transition fires the reminder.
 */

import type { PomodoroReminderKind } from "@/services/pomodoroService";

const kindToPhase: Record<PomodoroReminderKind, string> = {
  "work-start": "work_start",
  "break-start": "break_start",
  "break-end": "break_end",
  "rounds-done": "rounds_done",
};

export interface PomodoroReminderRequest {
  httpBase: string;
  bearer: string;
  kind: PomodoroReminderKind;
  /** Recently used lines the model should not repeat. */
  avoid: string[];
  /** Ask the Gateway to pre-synthesize speech (skip for system TTS). */
  synthesize: boolean;
  signal?: AbortSignal;
}

export interface PomodoroReminderResult {
  text: string;
  artifactId: string | null;
}

export async function fetchGeneratedPomodoroReminder(
  request: PomodoroReminderRequest,
): Promise<PomodoroReminderResult> {
  let response: Response;
  try {
    response = await fetch(`${request.httpBase}/api/pomodoro/reminders`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${request.bearer}`,
      },
      body: JSON.stringify({
        phase: kindToPhase[request.kind],
        avoid: request.avoid.slice(0, 12),
        synthesize: request.synthesize,
      }),
      signal: request.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new Error(`Pomodoro reminder gateway unreachable: ${String(error)}`);
  }

  if (!response.ok) {
    throw new Error(
      `Pomodoro reminder generation failed (HTTP ${response.status})`,
    );
  }

  const data = (await response.json()) as {
    text?: unknown;
    speech?: { artifact_id?: unknown } | null;
  };
  if (typeof data.text !== "string" || !data.text.trim()) {
    throw new Error("Pomodoro reminder response is missing text");
  }
  const artifactId =
    typeof data.speech?.artifact_id === "string"
      ? data.speech.artifact_id
      : null;
  return { text: data.text.trim(), artifactId };
}
