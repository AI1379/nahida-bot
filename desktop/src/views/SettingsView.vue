<script setup lang="ts">
import TtsSettingsPanel from "@/components/TtsSettingsPanel.vue";
import GatewayConnectionPanel from "@/components/GatewayConnectionPanel.vue";
import RemoteControlSettingsPanel from "@/components/RemoteControlSettingsPanel.vue";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();

function updateTtsSettings(next: typeof store.localConfig.ttsSettings) {
  store.updateTtsSettings(next);
}
</script>

<template>
  <section class="settings-view" aria-label="Desktop settings">
    <p v-if="store.persistenceError" class="settings-view__error" role="alert">
      {{ store.persistenceError }}
    </p>

    <div class="settings-view__grid">
      <GatewayConnectionPanel :runtime="props.runtime" />

      <RemoteControlSettingsPanel />

      <TtsSettingsPanel
        :settings="store.localConfig.ttsSettings"
        @update="updateTtsSettings"
        @preview="store.previewSystemSpeech"
      />
    </div>
  </section>
</template>
