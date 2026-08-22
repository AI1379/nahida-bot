import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pomodoroDefaults } from "@/domain/config";
import type { PomodoroSettings } from "@/domain/config";
import type { DesktopEvent } from "@/domain/runtime";
import {
  PomodoroService,
  idlePomodoroState,
} from "./pomodoroService";
import type { PomodoroState } from "./pomodoroService";
import { applyPomodoroCapability } from "./pomodoroCapability";
import { fetchGeneratedPomodoroReminder } from "./pomodoroTextService";

function createService(overrides: Partial<PomodoroSettings> = {}) {
  const settings: PomodoroSettings = {
    ...pomodoroDefaults,
    enabled: true,
    ...overrides,
  };
  const events: DesktopEvent[] = [];
  const states: PomodoroState[] = [];
  const service = new PomodoroService({
    getSettings: () => settings,
    onTick: (event) => events.push(event),
    onStateChange: (state) => states.push({ ...state }),
  });
  return { service, events, states, settings };
}

function reminders(events: DesktopEvent[]) {
  return events.filter(
    (event): event is DesktopEvent & { type: "notification.reminder" } =>
      event.type === "notification.reminder",
  );
}

const WORK_MS = pomodoroDefaults.workDurationMinutes * 60_000;
const BREAK_MS = pomodoroDefaults.breakDurationMinutes * 60_000;

describe("PomodoroService", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts a working phase even when reminders are disabled", () => {
    const { service, events, states } = createService({ enabled: false });

    service.start();

    expect(service.state.phase).toBe("working");
    expect(reminders(events)).toHaveLength(0);
    expect(states.at(-1)?.phase).toBe("working");
    expect(states.at(-1)?.expiresAt).not.toBeNull();
  });

  it("emits the work-start reminder with spoken playback by default", () => {
    const { service, events } = createService();

    service.start();

    const emitted = reminders(events);
    expect(emitted).toHaveLength(1);
    expect(emitted[0].message).toBe(pomodoroDefaults.workStartText);
    expect(emitted[0].source).toBe("local");
    expect(emitted[0].ttsEnabled).toBe(true);
    expect(typeof emitted[0].dedupeKey).toBe("string");
  });

  it("suppresses spoken playback when speakReminders is off", () => {
    const { service, events } = createService({ speakReminders: false });

    service.start();
    vi.advanceTimersByTime(WORK_MS);

    expect(reminders(events)).toHaveLength(2);
    for (const event of reminders(events)) {
      expect(event.ttsEnabled).toBe(false);
    }
  });

  it("prefers a dynamically generated reminder line when provided", () => {
    const generated = new Map([
      ["work-start", "新的专注时段开始啦，加油！"],
      ["break-start", null],
      ["break-end", ""],
    ]);
    const events: DesktopEvent[] = [];
    const serviceWithText = new PomodoroService({
      getSettings: () => ({ ...pomodoroDefaults, enabled: true }),
      onTick: (event) => events.push(event),
      getReminderText: (kind) => generated.get(kind) ?? null,
    });

    serviceWithText.start();
    expect(reminders(events).at(-1)?.message).toBe(
      "新的专注时段开始啦，加油！",
    );

    vi.advanceTimersByTime(WORK_MS);
    // break-start has no generated text → static fallback.
    expect(reminders(events).at(-1)?.message).toBe(
      pomodoroDefaults.breakStartText,
    );

    serviceWithText.stop();
  });

  it("moves from working to breaking and back to idle with reminders", () => {
    const { service, events, states } = createService();

    service.start();
    vi.advanceTimersByTime(WORK_MS);

    expect(service.state.phase).toBe("breaking");
    expect(states.at(-1)?.phase).toBe("breaking");
    expect(reminders(events).map((event) => event.message)).toEqual([
      pomodoroDefaults.workStartText,
      pomodoroDefaults.breakStartText,
    ]);

    vi.advanceTimersByTime(BREAK_MS);

    expect(service.state.phase).toBe("idle");
    expect(service.state.startedAt).toBeNull();
    expect(service.state.expiresAt).toBeNull();
    expect(reminders(events).map((event) => event.message)).toEqual([
      pomodoroDefaults.workStartText,
      pomodoroDefaults.breakStartText,
      pomodoroDefaults.roundsDoneText,
    ]);
  });

  it("runs multiple rounds automatically and stops after the last one", () => {
    const { service, events } = createService({ totalRounds: 2 });

    service.start();
    expect(service.state.round).toBe(1);
    expect(service.state.totalRounds).toBe(2);

    vi.advanceTimersByTime(WORK_MS);
    expect(service.state.phase).toBe("breaking");

    vi.advanceTimersByTime(BREAK_MS);
    expect(service.state.phase).toBe("working");
    expect(service.state.round).toBe(2);

    vi.advanceTimersByTime(WORK_MS);
    expect(service.state.phase).toBe("breaking");

    vi.advanceTimersByTime(BREAK_MS);
    expect(service.state.phase).toBe("idle");
    expect(service.state.round).toBe(0);
    expect(service.state.totalRounds).toBe(0);

    expect(reminders(events).map((event) => event.message)).toEqual([
      pomodoroDefaults.workStartText,
      pomodoroDefaults.breakStartText,
      pomodoroDefaults.breakEndText,
      pomodoroDefaults.workStartText,
      pomodoroDefaults.breakStartText,
      pomodoroDefaults.roundsDoneText,
    ]);

    // Every reminder across both rounds carries a distinct dedupe key.
    const keys = reminders(events).map((event) => event.dedupeKey);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("produces distinct dedupe keys across a full cycle", () => {
    const { service, events } = createService();

    service.start();
    vi.advanceTimersByTime(WORK_MS);
    vi.advanceTimersByTime(BREAK_MS);
    service.start();
    vi.advanceTimersByTime(WORK_MS);

    const keys = reminders(events).map((event) => event.dedupeKey);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("counts down remaining seconds from the phase deadline", () => {
    const { service } = createService();

    service.start();
    expect(service.state.remainingSeconds).toBe(
      pomodoroDefaults.workDurationMinutes * 60,
    );

    vi.advanceTimersByTime(60_000);
    expect(service.state.remainingSeconds).toBe(
      pomodoroDefaults.workDurationMinutes * 60 - 60,
    );
  });

  it("ignores start while already running and stop while idle", () => {
    const { service, events, states } = createService();

    service.start();
    const eventsAfterStart = events.length;
    service.start();
    expect(events).toHaveLength(eventsAfterStart);

    service.stop();
    expect(service.state).toEqual(idlePomodoroState);
    expect(states.at(-1)?.phase).toBe("idle");

    service.stop();
    expect(service.state).toEqual(idlePomodoroState);
  });

  it("stop cancels a pending phase transition", () => {
    const { service, events } = createService();

    service.start();
    service.stop();
    vi.advanceTimersByTime(WORK_MS + BREAK_MS);

    expect(reminders(events)).toHaveLength(1);
    expect(service.state.phase).toBe("idle");
  });

  it("suppresses reminders while the enabled switch is off mid-run", () => {
    const { service, events, settings } = createService();

    service.start();
    settings.enabled = false;
    vi.advanceTimersByTime(WORK_MS);
    vi.advanceTimersByTime(BREAK_MS);

    expect(reminders(events)).toHaveLength(1);
    expect(service.state.phase).toBe("idle");
  });

  it("toggle alternates between running and idle", () => {
    const { service } = createService();

    service.toggle();
    expect(service.state.phase).toBe("working");

    service.toggle();
    expect(service.state.phase).toBe("idle");
  });

  it("dispose clears the pending timer", () => {
    const { service, events } = createService();

    service.start();
    service.dispose();
    vi.advanceTimersByTime(WORK_MS);

    expect(reminders(events)).toHaveLength(1);
  });
});

describe("applyPomodoroCapability", () => {
  function createContext() {
    const settings: PomodoroSettings = { ...pomodoroDefaults, enabled: true };
    const updated: PomodoroSettings[] = [];
    const service = new PomodoroService({
      getSettings: () => settings,
      onTick: () => {},
    });
    const context = {
      service,
      getSettings: () => settings,
      updateSettings: (next: PomodoroSettings) => {
        Object.assign(settings, next);
        updated.push({ ...settings });
      },
    };
    return { context, settings, updated };
  }

  it("rejects unknown actions and invalid arguments", () => {
    const { context } = createContext();

    expect(applyPomodoroCapability(context, { action: "explode" })).toMatchObject({
      ok: false,
      error: { code: "invalid_arguments" },
    });
    expect(
      applyPomodoroCapability(context, { action: "start", workMinutes: 0 }),
    ).toMatchObject({ ok: false, error: { code: "invalid_arguments" } });
    expect(
      applyPomodoroCapability(context, { action: "start", workMinutes: 121 }),
    ).toMatchObject({ ok: false, error: { code: "invalid_arguments" } });
    expect(
      applyPomodoroCapability(context, { action: "start", totalRounds: 0 }),
    ).toMatchObject({ ok: false, error: { code: "invalid_arguments" } });
    expect(
      applyPomodoroCapability(context, { action: "start", totalRounds: 17 }),
    ).toMatchObject({ ok: false, error: { code: "invalid_arguments" } });
    expect(
      applyPomodoroCapability(context, { action: "start", enabled: "yes" }),
    ).toMatchObject({ ok: false, error: { code: "invalid_arguments" } });
  });

  it("configures settings and reports effective state", () => {
    const { context, settings, updated } = createContext();

    const result = applyPomodoroCapability(context, {
      action: "configure",
      workMinutes: 50,
      breakMinutes: 10,
      speakReminders: false,
      workStartText: "新的一轮开始！",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(settings.workDurationMinutes).toBe(50);
    expect(settings.breakDurationMinutes).toBe(10);
    expect(settings.speakReminders).toBe(false);
    expect(settings.workStartText).toBe("新的一轮开始！");
    expect(updated).toHaveLength(1);
    expect(result.result).toMatchObject({
      applied: "configure",
      settings: { workDurationMinutes: 50 },
    });
  });

  it("start applies the patch before starting the timer", () => {
    const { context, settings } = createContext();

    const result = applyPomodoroCapability(context, {
      action: "start",
      workMinutes: 30,
      totalRounds: 4,
    });

    expect(result.ok).toBe(true);
    expect(settings.workDurationMinutes).toBe(30);
    expect(settings.totalRounds).toBe(4);
    expect(context.service.state.phase).toBe("working");
    expect(context.service.state.round).toBe(1);
    expect(context.service.state.totalRounds).toBe(4);
    expect(context.service.state.remainingSeconds).toBe(30 * 60);
  });

  it("stop and status do not touch settings", () => {
    const { context, updated } = createContext();

    context.service.start();
    const stopped = applyPomodoroCapability(context, { action: "stop" });
    expect(stopped.ok).toBe(true);
    expect(context.service.state.phase).toBe("idle");

    const status = applyPomodoroCapability(context, { action: "status" });
    expect(status.ok).toBe(true);
    expect(updated).toHaveLength(0);
  });

  it("configures dynamicText alongside the other switches", () => {
    const { context, settings } = createContext();

    const result = applyPomodoroCapability(context, {
      action: "configure",
      dynamicText: true,
    });

    expect(result.ok).toBe(true);
    expect(settings.dynamicText).toBe(true);
  });
});

describe("fetchGeneratedPomodoroReminder", () => {
  const request = {
    httpBase: "http://127.0.0.1:6185",
    bearer: "token-1",
    kind: "break-start" as const,
    avoid: ["上一句", "再上一句"],
    synthesize: true,
  };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts the phase, avoid list and synthesize flag", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          phase: "break_start",
          text: "休息一下吧，伸展一下肩膀～",
          speech: { artifact_id: "art-9" },
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchGeneratedPomodoroReminder(request);

    expect(result.text).toBe("休息一下吧，伸展一下肩膀～");
    expect(result.artifactId).toBe("art-9");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:6185/api/pomodoro/reminders");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).authorization).toBe(
      "Bearer token-1",
    );
    expect(JSON.parse(String(init.body))).toEqual({
      phase: "break_start",
      avoid: ["上一句", "再上一句"],
      synthesize: true,
    });
  });

  it("rejects on HTTP errors and malformed payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("no model", { status: 503 })),
    );
    await expect(fetchGeneratedPomodoroReminder(request)).rejects.toThrow(
      /HTTP 503/,
    );

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ text: "" }))),
    );
    await expect(fetchGeneratedPomodoroReminder(request)).rejects.toThrow(
      /missing text/,
    );
  });
});
