/**
 * Client for the Gateway persona-grounded generation endpoint
 * (POST /api/generate/text). Called during the pomodoro phase runway so
 * both the generated line and its speech artifact are ready before the
 * phase transition fires the reminder.
 *
 * The task instruction is assembled here; the persona (SOUL.md etc.) is
 * injected server-side from the target workspace, so the reminder speaks
 * with the same voice as chat replies.
 */

import type { PomodoroReminderKind } from "@/services/pomodoroService";

const phaseScenes: Record<PomodoroReminderKind, string> = {
  "work-start": "番茄钟专注时段刚刚开始",
  "break-start": "专注时段结束、休息时段刚刚开始",
  "break-end": "休息时段结束、马上要开始下一轮专注",
  "rounds-done": "所有番茄钟轮次刚刚全部完成",
};

/** Must mirror the style the reminder segments send to /api/speech/jobs at
 * playback time, otherwise the pre-warmed cache key never matches. */
const reminderSpeechStyle = "neutral";

const reminderMaxChars = 40;

function buildReminderPrompt(kind: PomodoroReminderKind): string {
  return [
    `场景：${phaseScenes[kind]}。`,
    "请生成一句说给用户听的提醒语。要求：",
    "中文；不超过 40 个字；口语化、亲切自然；",
    "不要使用引号、emoji 或颜文字；不要以「纳西妲」开头；",
    "直接输出这一句话本身，不要任何解释或前缀。",
  ].join("\n");
}

export interface PomodoroReminderRequest {
  httpBase: string;
  bearer: string;
  kind: PomodoroReminderKind;
  /** Recently used lines the model should not repeat. */
  avoid: string[];
  /** Ask the Gateway to pre-synthesize speech (skip for system TTS). */
  synthesize: boolean;
  /**
   * Desktop-decided model spec (tag or provider/model); empty or omitted =
   * the Gateway-side default chain.
   */
  model?: string;
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
    response = await fetch(`${request.httpBase}/api/generate/text`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${request.bearer}`,
      },
      body: JSON.stringify({
        prompt: buildReminderPrompt(request.kind),
        max_chars: reminderMaxChars,
        avoid: request.avoid.slice(0, 12),
        model: request.model?.trim() ?? "",
        synthesize: request.synthesize,
        style: reminderSpeechStyle,
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
