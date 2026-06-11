<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { useDesktopStore } from "@/stores/desktop";
import PetRuntimeView from "@/views/PetRuntimeView.vue";
import WorkbenchView from "@/views/WorkbenchView.vue";

const store = useDesktopStore();
const activeView = ref<"runtime" | "workbench">("runtime");

const activeSegment = computed(() =>
  store.activePlan?.segments[store.currentSegmentIndex] ?? null,
);

const title = computed(() =>
  activeView.value === "runtime" ? "Pet Runtime" : "Development Workbench",
);

let segmentTimer: ReturnType<typeof setTimeout> | null = null;

function clearSegmentTimer() {
  if (segmentTimer !== null) {
    clearTimeout(segmentTimer);
    segmentTimer = null;
  }
}

function scheduleNextSegment() {
  clearSegmentTimer();
  if (!store.activePlan || !activeSegment.value) return;

  const current = activeSegment.value;
  const duration = Math.max(1400, current.text.length * 85);
  const pause = current.pauseAfterMs ?? 0;

  segmentTimer = setTimeout(() => {
    const nextIndex = store.currentSegmentIndex + 1;
    if (store.activePlan && nextIndex < store.activePlan.segments.length) {
      store.setSegment(nextIndex);
    } else {
      store.finishSpeaking();
    }
  }, duration + pause);
}

function selectModel(event: Event) {
  const target = event.target as HTMLSelectElement;
  store.selectModel(target.value);
}

watch(
  () => [store.activePlan, store.currentSegmentIndex] as const,
  () => scheduleNextSegment(),
);

onMounted(() => {
  store.startMockBackend();
});

onBeforeUnmount(() => {
  clearSegmentTimer();
  store.stopMockBackend();
});
</script>

<template>
  <main class="desktop-shell">
    <section class="hero-band">
      <div>
        <p>Nahida Desktop</p>
        <h1>{{ title }}</h1>
      </div>
      <div class="hero-band__actions">
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

    <PetRuntimeView v-if="activeView === 'runtime'" />
    <WorkbenchView v-else />
  </main>
</template>
