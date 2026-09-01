<script setup lang="ts">
import { computed, ref } from "vue";

import ControlPanel from "@/components/ControlPanel.vue";
import DisplayPlanPanel from "@/components/DisplayPlanPanel.vue";
import ExpressionMappingPanel from "@/components/ExpressionMappingPanel.vue";
import Live2DStage from "@/components/Live2DStage.vue";
import MotionMappingPanel from "@/components/MotionMappingPanel.vue";
import MotionPerformancePanel from "@/components/MotionPerformancePanel.vue";
import MotionHistoryPanel from "@/components/MotionHistoryPanel.vue";
import PortableMotionPanel from "@/components/PortableMotionPanel.vue";
import TtsSettingsPanel from "@/components/TtsSettingsPanel.vue";
import DesktopPluginSettingsHost from "@/components/DesktopPluginSettingsHost.vue";
import TranscriptPanel from "@/components/TranscriptPanel.vue";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import type { NormalizedMotionClip } from "@/domain/normalizedPose";
import type { PortableMotionTargetModel } from "@/domain/portableMotion";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();
const live2dStage = ref<{
  replayNormalizedClip: (clip: NormalizedMotionClip) => boolean;
} | null>(null);
const replayStatus = ref("");
const replayedPlanId = ref("");
const portableMotionTarget = ref<PortableMotionTargetModel | null>(null);
const portablePreviewStatus = ref("");

const activeSegment = computed(
  () => store.activePlan?.segments[store.currentSegmentIndex] ?? null,
);
const previewRenderMode = computed(() =>
  store.petRuntime.renderMode === "suspended"
    ? "idle"
    : store.petRuntime.renderMode,
);

function replayMotion(playback: MotionPlaybackSummary): void {
  const applied = live2dStage.value?.replayNormalizedClip(
    playback.normalizedClip,
  ) ?? false;
  replayStatus.value = applied
    ? `Replaying ${playback.primitive} from ${new Date(playback.timestamp).toLocaleString()}.`
    : "Live2D is not ready to replay this motion yet.";
  replayedPlanId.value = applied ? playback.motionPlanId : "";
}

function previewPortableMotion(clip: NormalizedMotionClip): void {
  const applied = live2dStage.value?.replayNormalizedClip(clip) ?? false;
  portablePreviewStatus.value = applied
    ? `Preview accepted: ${clip.id} on ${store.model.name}.`
    : "Live2D is not ready to preview this motion.";
}
</script>

<template>
  <section class="workspace" aria-label="Development workbench">
    <Live2DStage
      ref="live2dStage"
      :emotion="store.currentEmotion"
      :expression-key="store.currentExpressionKey"
      :motion="store.currentMotion"
      :render-mode="previewRenderMode"
      renderer-profile="preview"
      :model="store.model"
      :speaking="store.speaking"
      :motion-data-collection-enabled="store.localConfig.motionDataCollectionEnabled"
      :motion-telemetry-enabled="false"
      playback-surface="workbench"
      :caption-text="activeSegment?.text ?? ''"
      :expression-map-version="store.expressionMapVersion"
      :motion-map-version="store.motionMapVersion"
      @expressions-loaded="store.setModelExpressions"
      @motions-loaded="store.setModelMotions"
      @portable-motion-target-loaded="portableMotionTarget = $event"
    />

    <aside class="side-rail">
      <ControlPanel
        :connected="store.connected"
        :gateway-url="store.gatewayUrl"
        @connect="props.runtime.connectMockBackend"
        @disconnect="props.runtime.disconnectMockBackend"
        @submit="props.runtime.submitUserMessage"
        @submit-mock-llm-result="props.runtime.submitMockLlmResult"
      />
      <DisplayPlanPanel
        :plan="store.activePlan"
        :active-index="store.currentSegmentIndex"
      />
      <TtsSettingsPanel
        :settings="store.localConfig.ttsSettings"
        @update="store.updateTtsSettings"
        @preview="store.previewSystemSpeech"
      />
      <DesktopPluginSettingsHost
        :host="props.runtime.desktopPlugins"
        placement="workbench"
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
      <MotionPerformancePanel
        :profile="store.model.performanceProfile!"
        @update="store.updateModelPerformanceProfile"
      />
      <PortableMotionPanel
        :target="portableMotionTarget"
        :preview-status="portablePreviewStatus"
        @preview="previewPortableMotion"
        @reset-preview-status="portablePreviewStatus = ''"
      />
      <MotionHistoryPanel
        :recent="store.recentMotionPlaybacks"
        :feedback-enabled="store.localConfig.motionDataCollectionEnabled"
        :replay-status="replayStatus"
        :replayed-plan-id="replayedPlanId"
        @replay="replayMotion"
      />
      <TranscriptPanel :entries="store.transcript" />
    </aside>
  </section>
</template>
