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
    <header class="settings-view__intro">
      <div>
        <p class="settings-view__eyebrow">Desktop preferences</p>
        <p class="settings-view__description">
          Configure the Gateway connection, voice playback, and local access
          boundaries for this device.
        </p>
      </div>
      <span class="settings-view__privacy">Stored locally</span>
    </header>

    <p v-if="store.persistenceError" class="settings-view__error" role="alert">
      {{ store.persistenceError }}
    </p>

    <div class="settings-view__grid">
      <div class="settings-view__column settings-view__column--primary">
        <GatewayConnectionPanel :runtime="props.runtime" />
      </div>

      <aside class="settings-view__column settings-view__column--secondary">
        <TtsSettingsPanel
          :settings="store.localConfig.ttsSettings"
          @update="updateTtsSettings"
          @preview="store.previewSystemSpeech"
        />

        <RemoteControlSettingsPanel />
      </aside>
    </div>
  </section>
</template>
