import { shallowRef, type ShallowRef } from "vue";

import type { PomodoroSettings } from "@/domain/config";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import type { CapabilityExecutionResult } from "@/domain/runtime";
import type {
  DesktopPluginDefinition,
  DesktopPluginRuntime,
} from "@/plugins/desktopPluginHost";
import { applyPomodoroCapability } from "@/services/pomodoroCapability";
import {
  idlePomodoroState,
  PomodoroService,
  type PomodoroState,
} from "@/services/pomodoroService";
import { surfaceFromPomodoroState } from "@/services/pomodoroSurface";
import PomodoroPluginSettingsPanel from "./PomodoroPluginSettingsPanel.vue";
import {
  POMODORO_CONTROL_CAPABILITY,
  POMODORO_PLUGIN_ID,
  POMODORO_TIMER_SURFACE_ID,
  pomodoroPluginActions,
} from "./manifest";
import { PomodoroReminderPrefetcher } from "./reminderPrefetcher";

export interface PomodoroDesktopPluginDependencies {
  getSettings(): PomodoroSettings;
  updateSettings(settings: PomodoroSettings): void;
  getGatewayConnection(): GatewayConnectionSettings;
}

export interface PomodoroDesktopPluginRuntime extends DesktopPluginRuntime {
  state: ShallowRef<PomodoroState>;
}

export function createPomodoroDesktopPlugin(
  dependencies: PomodoroDesktopPluginDependencies,
): DesktopPluginDefinition {
  return {
    manifest: {
      id: POMODORO_PLUGIN_ID,
      name: "Pomodoro",
      version: "0.1.0",
      entrypoint: "builtin:nahida.pomodoro",
      builtin: true,
      contributes: {
        capabilities: [POMODORO_CONTROL_CAPABILITY],
        actions: [...pomodoroPluginActions],
        surfaces: [
          {
            id: POMODORO_TIMER_SURFACE_ID,
            target: "pet.overlay",
            kind: "countdown",
            priority: 50,
          },
        ],
        settingsPanels: [
          {
            id: "settings",
            section: {
              id: "focus",
              label: "专注",
              hint: "番茄钟与提醒",
              order: 40,
            },
            placements: ["settings", "workbench"],
            component: PomodoroPluginSettingsPanel,
          },
        ],
      },
    },
    activate(context): PomodoroDesktopPluginRuntime {
      const state = shallowRef<PomodoroState>({ ...idlePomodoroState });
      const reminders = new PomodoroReminderPrefetcher(dependencies);

      const service = new PomodoroService({
        getSettings: dependencies.getSettings,
        onTick: (event) => context.emitEvent(event),
        onStateChange: (next) => {
          state.value = next;
          const surface = surfaceFromPomodoroState(next);
          if (surface) {
            context.setSurface(POMODORO_TIMER_SURFACE_ID, surface.view);
          } else {
            context.removeSurface(POMODORO_TIMER_SURFACE_ID);
          }
          reminders.schedule(next);
        },
        getReminderText: (kind) => reminders.get(kind),
      });

      const action = (name: (typeof pomodoroPluginActions)[number]) => {
        if (name === "start") service.start();
        if (name === "stop") service.stop();
        if (name === "toggle") service.toggle();
        return applied(name, service.state);
      };

      return {
        state,
        capabilities: {
          [POMODORO_CONTROL_CAPABILITY]: (args) =>
            applyPomodoroCapability(
              {
                service,
                getSettings: dependencies.getSettings,
                updateSettings: dependencies.updateSettings,
              },
              args,
            ),
        },
        actions: {
          start: () => action("start"),
          stop: () => action("stop"),
          toggle: () => action("toggle"),
        },
        dispose(): void {
          service.dispose();
          reminders.dispose();
        },
      };
    },
  };
}

function applied(
  action: string,
  state: PomodoroState,
): CapabilityExecutionResult {
  return {
    ok: true,
    result: { applied: action, state },
  };
}
