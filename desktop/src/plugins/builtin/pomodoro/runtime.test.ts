import { afterEach, describe, expect, it, vi } from "vitest";

import { pomodoroDefaults, type PomodoroSettings } from "@/domain/config";
import { defaultGatewayConnectionSettings } from "@/domain/gatewayConnection";
import { DesktopPluginHost } from "@/plugins/desktopPluginHost";
import {
  POMODORO_CONTROL_CAPABILITY,
  POMODORO_PLUGIN_ID,
} from "./manifest";
import {
  createPomodoroDesktopPlugin,
  type PomodoroDesktopPluginRuntime,
} from "./runtime";

afterEach(() => {
  vi.useRealTimers();
});

describe("Pomodoro Desktop plugin", () => {
  it("activates timer actions, capability, and local surface through the host", () => {
    vi.useFakeTimers();
    let settings: PomodoroSettings = {
      ...pomodoroDefaults,
      enabled: false,
    };
    const upsertSurface = vi.fn();
    const removeSurface = vi.fn();
    const host = new DesktopPluginHost({
      emitEvent: vi.fn(),
      upsertSurface,
      removeSurface,
    });
    const record = host.activate(
      createPomodoroDesktopPlugin({
        getSettings: () => settings,
        updateSettings: (next) => {
          settings = next;
        },
        getGatewayConnection: () => ({ ...defaultGatewayConnectionSettings }),
      }),
    );

    expect(record.status).toBe("active");
    expect(host.invokeAction(POMODORO_PLUGIN_ID, "start")).toMatchObject({
      ok: true,
      result: { applied: "start" },
    });
    expect(upsertSurface).toHaveBeenCalledWith(
      expect.objectContaining({
        ownerPluginId: POMODORO_PLUGIN_ID,
        id: "timer",
        target: "pet.overlay",
        kind: "countdown",
      }),
    );
    const runtime = host.getRuntime(
      POMODORO_PLUGIN_ID,
    ) as PomodoroDesktopPluginRuntime;
    expect(runtime.state.value.phase).toBe("working");
    expect(host.executeCapability(POMODORO_CONTROL_CAPABILITY, {
      action: "status",
    })).toMatchObject({
      ok: true,
      result: { applied: "status", state: { phase: "working" } },
    });

    host.dispose();
    expect(removeSurface).toHaveBeenCalledWith(POMODORO_PLUGIN_ID, "timer");
  });
});
