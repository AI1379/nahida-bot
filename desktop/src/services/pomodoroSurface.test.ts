import { describe, expect, it } from "vitest";

import { surfaceFromPomodoroState } from "./pomodoroSurface";
import { idlePomodoroState } from "./pomodoroService";

describe("surfaceFromPomodoroState", () => {
  it("keeps the local timer out of the surface host while idle", () => {
    expect(surfaceFromPomodoroState(idlePomodoroState)).toBeNull();
  });

  it("projects a working timer into the shared Desktop surface contract", () => {
    expect(
      surfaceFromPomodoroState({
        phase: "working",
        round: 2,
        totalRounds: 4,
        startedAt: "2026-08-30T00:00:00.000Z",
        expiresAt: "2026-08-30T00:25:00.000Z",
        remainingSeconds: 1500,
      }),
    ).toMatchObject({
      ownerPluginId: "nahida.pomodoro",
      id: "timer",
      target: "pet.overlay",
      kind: "countdown",
      source: "local",
      view: {
        title: "专注",
        status: "进行中",
        detail: "2/4",
        expiresAt: "2026-08-30T00:25:00.000Z",
        tone: "info",
      },
    });
  });
});
