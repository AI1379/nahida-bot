<script setup lang="ts">
import { computed, ref } from "vue";
import { isTauri } from "@tauri-apps/api/core";

import Live2DStage from "@/components/Live2DStage.vue";
import MotionFeedbackPanel from "@/components/MotionFeedbackPanel.vue";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();
const replyText = ref("");
const recordsLocalStage = !isTauri();

const activeSegment = computed(
  () => store.activePlan?.segments[store.currentSegmentIndex] ?? null,
);
const latestPlayback = computed(
  () => store.recentMotionPlaybacks[0] ?? null,
);
const previewRenderMode = computed(() =>
  store.petRuntime.renderMode === "suspended"
    ? "idle"
    : store.petRuntime.renderMode,
);

function submitReply() {
  const trimmed = replyText.value.trim();
  if (!trimmed) return;
  props.runtime.submitUserMessage(trimmed);
  replyText.value = "";
}

function handleMotionExecuted(playback: MotionPlaybackSummary): void {
  store.rememberMotionPlayback(playback);
}
</script>

<template>
  <section class="pet-runtime" aria-label="Pet runtime">
    <Live2DStage
      :emotion="store.currentEmotion"
      :expression-key="store.currentExpressionKey"
      :motion="store.currentMotion"
      :render-mode="previewRenderMode"
      :model="store.model"
      :speaking="store.speaking"
      :motion-data-collection-enabled="store.localConfig.motionDataCollectionEnabled"
      :motion-telemetry-enabled="recordsLocalStage"
      playback-surface="runtime"
      :caption-text="activeSegment?.text ?? ''"
      :expression-map-version="store.expressionMapVersion"
      :motion-map-version="store.motionMapVersion"
      :debug-enabled="false"
      :dev-chrome="false"
      @expressions-loaded="store.setModelExpressions"
      @motions-loaded="store.setModelMotions"
      @motion-executed="handleMotionExecuted"
    />

    <MotionFeedbackPanel
      class="pet-runtime__motion-feedback"
      :playback="latestPlayback"
      :enabled="store.localConfig.motionDataCollectionEnabled"
      compact
    />

    <form class="pet-runtime__composer" @submit.prevent="submitReply">
      <input
        v-model="replyText"
        type="text"
        :disabled="!store.connected"
        placeholder="Reply"
      />
      <button type="submit" :disabled="!store.connected || !replyText.trim()">
        Send
      </button>
    </form>
  </section>
</template>
