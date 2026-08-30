import type { PomodoroSettings } from "@/domain/config";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import type { DesktopPluginDefinition } from "@/plugins/desktopPluginHost";
import { sanitizePomodoroSettings } from "@/services/pomodoroSettingsStorage";
import { POMODORO_PLUGIN_ID } from "./pomodoro/manifest";
import { createPomodoroDesktopPlugin } from "./pomodoro/runtime";

export interface BuiltinDesktopPluginDependencies {
  getPluginSettings(pluginId: string): unknown;
  updatePluginSettings(pluginId: string, settings: unknown): void;
  getGatewayConnection(): GatewayConnectionSettings;
}

export function createBuiltinDesktopPlugins(
  dependencies: BuiltinDesktopPluginDependencies,
): DesktopPluginDefinition[] {
  return [
    createPomodoroDesktopPlugin({
      getSettings: () =>
        sanitizePomodoroSettings(
          dependencies.getPluginSettings(POMODORO_PLUGIN_ID),
        ),
      updateSettings: (settings: PomodoroSettings) =>
        dependencies.updatePluginSettings(POMODORO_PLUGIN_ID, settings),
      getGatewayConnection: dependencies.getGatewayConnection,
    }),
  ];
}
