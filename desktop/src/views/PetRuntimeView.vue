<script setup lang="ts">
import { computed, ref } from "vue";

import Live2DStage from "@/components/Live2DStage.vue";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();
const replyText = ref("");

const activeSegment = computed(
  () => store.activePlan?.segments[store.currentSegmentIndex] ?? null,
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
      :caption-text="activeSegment?.text ?? ''"
      :expression-map-version="store.expressionMapVersion"
      :motion-map-version="store.motionMapVersion"
      :debug-enabled="false"
      :dev-chrome="false"
      @expressions-loaded="store.setModelExpressions"
      @motions-loaded="store.setModelMotions"
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
