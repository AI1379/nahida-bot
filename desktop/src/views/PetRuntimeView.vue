<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { isTauri } from "@tauri-apps/api/core";
import { useMediaQuery } from "@vueuse/core";

import Live2DStage from "@/components/Live2DStage.vue";
import MotionFeedbackPanel from "@/components/MotionFeedbackPanel.vue";
import RuntimeConversationPanel from "@/components/RuntimeConversationPanel.vue";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();
const replyText = ref("");
const submitting = ref(false);
const replayedPlanId = ref("");
const wideLayout = useMediaQuery("(min-width: 921px)");
const conversationOpen = ref(wideLayout.value);
const recordsLocalStage = !isTauri();
const live2dStage = ref<{
  replayNormalizedClip: (
    clip: MotionPlaybackSummary["normalizedClip"],
  ) => boolean;
} | null>(null);

const activeSegment = computed(
  () => store.activePlan?.segments[store.currentSegmentIndex] ?? null,
);
const latestPlayback = computed(
  () => store.latestMotionFeedbackPlayback,
);
const previewRenderMode = computed(() =>
  store.petRuntime.renderMode === "suspended"
    ? "idle"
    : store.petRuntime.renderMode,
);

watch(wideLayout, (wide) => {
  conversationOpen.value = wide;
});

async function submitReply() {
  const trimmed = replyText.value.trim();
  if (!trimmed || submitting.value) return;
  submitting.value = true;
  const result = await props.runtime.submitUserMessage(trimmed);
  if (result.ok) replyText.value = "";
  submitting.value = false;
}

function handleMotionExecuted(playback: MotionPlaybackSummary): void {
  store.rememberMotionPlayback(playback);
}

function replayMotion(playback: MotionPlaybackSummary): void {
  if (live2dStage.value?.replayNormalizedClip(playback.normalizedClip)) {
    replayedPlanId.value = playback.motionPlanId;
  }
}
</script>

<template>
  <section class="pet-runtime" aria-label="桌宠运行界面">
    <Live2DStage
      ref="live2dStage"
      :emotion="store.currentEmotion"
      :expression-key="store.currentExpressionKey"
      :motion="store.currentMotion"
      :render-mode="previewRenderMode"
      renderer-profile="preview"
      :model="store.model"
      :speaking="store.speaking"
      :motion-duration-ms="store.petRuntime.segmentDurationMs"
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

    <button
      v-if="!conversationOpen"
      type="button"
      class="pet-runtime__conversation-toggle"
      aria-controls="runtime-conversation"
      :aria-expanded="false"
      @click="conversationOpen = true"
    >
      查看对话
      <span v-if="store.currentSessionTurns.length">
        {{ store.currentSessionTurns.length }}
      </span>
    </button>

    <RuntimeConversationPanel
      v-if="conversationOpen"
      :session-id="store.sessionId"
      :turns="store.currentSessionTurns"
      closable
      @close="conversationOpen = false"
    />

    <MotionFeedbackPanel
      v-if="latestPlayback"
      class="pet-runtime__motion-feedback"
      :playback="latestPlayback"
      :enabled="store.localConfig.motionDataCollectionEnabled"
      compact
      collapsible
      initially-collapsed
      replayable
      rating-surface="runtime"
      :replay-of="replayedPlanId === latestPlayback?.motionPlanId ? replayedPlanId : undefined"
      @replay="replayMotion"
    />

    <form class="pet-runtime__composer" @submit.prevent="submitReply">
      <input
        v-model="replyText"
        type="text"
        :disabled="!store.connected || submitting"
        placeholder="给纳西妲发消息"
        aria-label="给纳西妲发消息"
      />
      <button
        type="submit"
        :disabled="!store.connected || submitting || !replyText.trim()"
      >
        {{ submitting ? "发送中…" : "发送" }}
      </button>
    </form>
  </section>
</template>
