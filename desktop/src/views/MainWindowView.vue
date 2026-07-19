<script setup lang="ts">
import {
  computed,
  ref,
} from "vue";
import { useDesktopRuntimeController } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";
import PetRuntimeView from "@/views/PetRuntimeView.vue";
import SettingsView from "@/views/SettingsView.vue";
import WorkbenchView from "@/views/WorkbenchView.vue";

const store = useDesktopStore();
const runtime = useDesktopRuntimeController(store);

type DesktopView = "runtime" | "workbench" | "settings";
const activeView = ref<DesktopView>("runtime");

const title = computed(() => {
  switch (activeView.value) {
    case "workbench":
      return "Development Workbench";
    case "settings":
      return "Settings";
    default:
      return "Pet Runtime";
  }
});

function selectModel(event: Event) {
  const target = event.target as HTMLSelectElement;
  store.selectModel(target.value);
}
</script>

<template>
  <main class="desktop-shell">
    <section class="hero-band">
      <div>
        <p>Nahida Desktop</p>
        <h1>{{ title }}</h1>
      </div>
      <div class="hero-band__actions">
        <div class="pet-controls" aria-label="Pet window controls">
          <button type="button" @click="store.requestPetEmerge()">
            Emerge
          </button>
          <button type="button" @click="store.enterPetChat()">Chat</button>
          <button type="button" @click="store.requestPetRetreat()">
            Retreat
          </button>
        </div>
        <div class="view-switch" role="tablist" aria-label="Desktop view">
          <button
            type="button"
            :class="{ 'is-active': activeView === 'runtime' }"
            @click="activeView = 'runtime'"
          >
            Runtime
          </button>
          <button
            type="button"
            :class="{ 'is-active': activeView === 'workbench' }"
            @click="activeView = 'workbench'"
          >
            Workbench
          </button>
          <button
            type="button"
            :class="{ 'is-active': activeView === 'settings' }"
            @click="activeView = 'settings'"
          >
            Settings
          </button>
        </div>
        <label class="model-picker" for="live2d-model-picker">
          <span>Model</span>
          <select
            id="live2d-model-picker"
            :value="store.selectedModelId"
            @change="selectModel"
          >
            <option
              v-for="model in store.models"
              :key="model.id"
              :value="model.id"
            >
              {{ model.name }}
            </option>
          </select>
        </label>
        <div class="connection-pill" :data-connected="store.connected">
          {{ store.connected ? store.petRuntime.status : "Disconnected" }}
        </div>
      </div>
    </section>

    <PetRuntimeView v-if="activeView === 'runtime'" :runtime="runtime" />
    <WorkbenchView v-else-if="activeView === 'workbench'" :runtime="runtime" />
    <SettingsView v-else :runtime="runtime" />
  </main>
</template>
