import type { PluginSurfaceContribution } from "@/domain/pluginSurface";
import type { PomodoroState } from "@/services/pomodoroService";

export const localPomodoroSurfaceIdentity = {
  ownerPluginId: "nahida.pomodoro",
  id: "timer",
} as const;

export function surfaceFromPomodoroState(
  state: PomodoroState,
): PluginSurfaceContribution | null {
  if (state.phase === "idle") return null;
  return {
    ...localPomodoroSurfaceIdentity,
    target: "pet.overlay",
    kind: "countdown",
    priority: 50,
    source: "local",
    view: {
      title: state.phase === "breaking" ? "休息" : "专注",
      text: "",
      status: state.phase === "breaking" ? "休息中" : "进行中",
      detail:
        state.round > 0 && state.totalRounds > 0
          ? `${state.round}/${state.totalRounds}`
          : "",
      expiresAt: state.expiresAt ?? "",
      progress: null,
      items: [],
      tone: state.phase === "breaking" ? "success" : "info",
    },
  };
}
