<script setup lang="ts">
import { computed } from "vue";

import ControlPanel from "@/components/ControlPanel.vue";
import DisplayPlanPanel from "@/components/DisplayPlanPanel.vue";
import ExpressionMappingPanel from "@/components/ExpressionMappingPanel.vue";
import Live2DStage from "@/components/Live2DStage.vue";
import MotionMappingPanel from "@/components/MotionMappingPanel.vue";
import TtsSettingsPanel from "@/components/TtsSettingsPanel.vue";
import TranscriptPanel from "@/components/TranscriptPanel.vue";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();

const activeSegment = computed(
  () => store.activePlan?.segments[store.currentSegmentIndex] ?? null,
);
</script>

<template>
  <section class="workspace" aria-label="Development workbench">
    <Live2DStage
      :emotion="store.currentEmotion"
      :expression-key="store.currentExpressionKey"
      :motion="store.currentMotion"
      :render-mode="store.petRuntime.renderMode"
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
</template>
