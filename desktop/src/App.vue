<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from "vue";

import ControlPanel from "@/components/ControlPanel.vue";
import DisplayPlanPanel from "@/components/DisplayPlanPanel.vue";
import ExpressionMappingPanel from "@/components/ExpressionMappingPanel.vue";
import Live2DStage from "@/components/Live2DStage.vue";
import MotionMappingPanel from "@/components/MotionMappingPanel.vue";
import TranscriptPanel from "@/components/TranscriptPanel.vue";
import { useDesktopStore } from "@/stores/desktop";

const store = useDesktopStore();

const activeSegment = computed(() =>
  store.activePlan?.segments[store.currentSegmentIndex] ?? null,
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
        <h1>Live2D Runtime Scaffold</h1>
      </div>
      <div class="hero-band__actions">
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
          {{ store.connected ? "Mock backend connected" : "Disconnected" }}
        </div>
      </div>
    </section>

    <section class="workspace">
      <Live2DStage
        :emotion="store.currentEmotion"
        :expression-key="store.currentExpressionKey"
        :motion="store.currentMotion"
        :model="store.model"
        :speaking="store.speaking"
        :caption-text="activeSegment?.text ?? ''"
        :expression-map-version="store.expressionMapVersion"
        :motion-map-version="store.motionMapVersion"
        @expressions-loaded="store.setModelExpressions"
        @motions-loaded="store.setModelMotions"
      />

      <aside class="side-rail">
        <ControlPanel
          :connected="store.connected"
          :gateway-url="store.gatewayUrl"
          @connect="store.startMockBackend"
          @disconnect="store.stopMockBackend"
          @submit="store.submitUserMessage"
          @submit-mock-llm-result="store.submitMockLlmResult"
        />
        <DisplayPlanPanel
          :plan="store.activePlan"
          :active-index="store.currentSegmentIndex"
        />
        <ExpressionMappingPanel
          :model="store.model"
          :expressions="store.expressionOptions"
          @add-mapping="store.addExpressionKeywordMapping"
          @remove-mapping="store.removeExpressionKeywordMapping"
          @update-mapping="store.setExpressionKeywordMapping"
          @preview="store.previewExpressionKeyword"
        />
        <MotionMappingPanel
          :model="store.model"
          :motions="store.motionOptions"
          @update-mapping="store.setMotionMapping"
          @preview="store.previewMotion"
        />
        <TranscriptPanel :entries="store.transcript" />
      </aside>
    </section>
  </main>
</template>
