<script setup lang="ts">
import { computed } from "vue";

import TtsSettingsPanel from "@/components/TtsSettingsPanel.vue";
import GatewayConnectionPanel from "@/components/GatewayConnectionPanel.vue";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();

const statusSummary = computed(() => {
  if (store.gatewayConnection.mode === "mock") {
    return store.connected
      ? "Mock backend is running."
      : "Mock backend is offline.";
  }
  if (store.gatewayConnectionError) {
    return store.gatewayConnectionError;
  }
  if (!store.gatewayConnection.nodeToken) {
    return "Gateway mode requires a node token or a pairing token.";
  }
  return store.connected
    ? `Connected to ${store.gatewayConnection.gatewayWsUrl}`
    : `Not connected to ${store.gatewayConnection.gatewayWsUrl}`;
});

function updateTtsSettings(next: typeof store.localConfig.ttsSettings) {
  store.updateTtsSettings(next);
}
</script>

<template>
  <section class="settings-view" aria-label="Desktop settings">
    <header class="settings-view__summary">
      <div>
        <p>Nahida Desktop</p>
        <h1>Settings</h1>
      </div>
      <div
        class="settings-view__pill"
        :data-state="
          store.connected
            ? 'connected'
            : store.gatewayConnectionError
              ? 'error'
              : 'offline'
        "
      >
        {{ statusSummary }}
      </div>
    </header>

    <div class="settings-view__grid">
      <GatewayConnectionPanel :runtime="props.runtime" />

      <TtsSettingsPanel
        :settings="store.localConfig.ttsSettings"
        @update="updateTtsSettings"
        @preview="store.previewSystemSpeech"
      />
    </div>
  </section>
</template>
