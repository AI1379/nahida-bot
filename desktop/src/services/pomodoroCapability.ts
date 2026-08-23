/**
 * Renderer-side handler for the `desktop.pomodoro.control` capability.
 * The Gateway routes agent tool invocations here so the agent can start,
 * stop, configure, or query the local pomodoro timer on the user's Desktop.
 */

import type { CapabilityExecutionResult } from "@/domain/runtime";
import type { PomodoroSettings } from "@/domain/config";
import type { PomodoroService } from "./pomodoroService";

export const POMODORO_CONTROL_CAPABILITY = "desktop.pomodoro.control";

const pomodoroActions = new Set([
  "start",
  "stop",
  "toggle",
  "status",
  "configure",
]);

const pomodoroTextFields = [
  "workStartText",
  "breakStartText",
  "breakEndText",
  "roundsDoneText",
] as const;

export interface PomodoroCapabilityContext {
  service: PomodoroService;
  getSettings: () => PomodoroSettings;
  updateSettings: (settings: PomodoroSettings) => void;
}

export function applyPomodoroCapability(
  context: PomodoroCapabilityContext,
  args: Record<string, unknown>,
): CapabilityExecutionResult {
  const action = typeof args.action === "string" ? args.action : "";
  if (!pomodoroActions.has(action)) {
    return invalid(
      "action must be one of start, stop, toggle, status, configure",
    );
  }

  const patch: Partial<PomodoroSettings> = {};

  const durationError = readDuration(args, "workMinutes", 1, 120, (minutes) => {
    patch.workDurationMinutes = minutes;
  }) ?? readDuration(args, "breakMinutes", 1, 60, (minutes) => {
    patch.breakDurationMinutes = minutes;
  }) ?? readDuration(args, "totalRounds", 1, 16, (rounds) => {
    patch.totalRounds = rounds;
  });
  if (durationError) return invalid(durationError);

  for (const field of pomodoroTextFields) {
    const value = args[field];
    if (value === undefined) continue;
    if (typeof value !== "string" || !value.trim() || value.length > 200) {
      return invalid(`${field} must be a non-empty string of at most 200 characters`);
    }
    patch[field] = value.trim();
  }

  for (const field of ["enabled", "speakReminders", "dynamicText"] as const) {
    const value = args[field];
    if (value === undefined) continue;
    if (typeof value !== "boolean") {
      return invalid(`${field} must be a boolean`);
    }
    patch[field] = value;
  }

  // Unlike the static texts, the model spec may be empty (clears back to
  // the Gateway-side default chain).
  if (args.dynamicTextModel !== undefined) {
    const model = args.dynamicTextModel;
    if (typeof model !== "string" || model.length > 128) {
      return invalid(
        "dynamicTextModel must be a string of at most 128 characters",
      );
    }
    patch.dynamicTextModel = model.trim();
  }

  if (Object.keys(patch).length > 0) {
    context.updateSettings({ ...context.getSettings(), ...patch });
  }

  switch (action) {
    case "start":
      context.service.start();
      break;
    case "stop":
      context.service.stop();
      break;
    case "toggle":
      context.service.toggle();
      break;
  }

  return {
    ok: true,
    result: {
      applied: action,
      state: context.service.state,
      settings: context.getSettings(),
    },
  };
}

function readDuration(
  args: Record<string, unknown>,
  field: string,
  minimum: number,
  maximum: number,
  apply: (minutes: number) => void,
): string | null {
  const value = args[field];
  if (value === undefined) return null;
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < minimum ||
    value > maximum ||
    !Number.isInteger(value)
  ) {
    return `${field} must be an integer between ${minimum} and ${maximum}`;
  }
  apply(value);
  return null;
}

function invalid(message: string): CapabilityExecutionResult {
  return {
    ok: false,
    error: {
      code: "invalid_arguments",
      message: `${POMODORO_CONTROL_CAPABILITY}: ${message}`,
      retryable: false,
    },
  };
}
