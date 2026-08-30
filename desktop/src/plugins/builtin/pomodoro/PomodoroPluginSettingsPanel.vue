<script setup lang="ts">
import { computed } from "vue";

import PomodoroSettingsPanel from "@/components/PomodoroSettingsPanel.vue";
import type {
  DesktopPluginHost,
  DesktopPluginRuntime,
} from "@/plugins/desktopPluginHost";
import { sanitizePomodoroSettings } from "@/services/pomodoroSettingsStorage";
import { useDesktopStore } from "@/stores/desktop";
import { POMODORO_PLUGIN_ID } from "./manifest";
import type { PomodoroDesktopPluginRuntime } from "./runtime";

const props = defineProps<{
  host: DesktopPluginHost;
  pluginId: string;
  runtime: DesktopPluginRuntime;
}>();

const store = useDesktopStore();
const pomodoroRuntime = props.runtime as PomodoroDesktopPluginRuntime;
const state = computed(() => pomodoroRuntime.state.value);
const settings = computed(() =>
  sanitizePomodoroSettings(store.desktopPluginSettings[POMODORO_PLUGIN_ID]),
);

function updateSettings(next: ReturnType<typeof sanitizePomodoroSettings>): void {
  store.updateDesktopPluginSettings(POMODORO_PLUGIN_ID, next);
}

function invoke(action: "start" | "stop"): void {
  props.host.invokeAction(props.pluginId, action);
}
</script>

<template>
  <PomodoroSettingsPanel
    :settings="settings"
    :state="state"
    @update="updateSettings"
    @start="invoke('start')"
    @stop="invoke('stop')"
  />
</template>
