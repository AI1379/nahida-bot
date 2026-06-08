<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { DisplayEmotion, DisplayMotion } from "@/domain/displayPlan";
import type { Live2DModelManifest } from "@/domain/live2d";
import Live2DDebugPanel from "@/components/Live2DDebugPanel.vue";
import type { Live2DDebugSnapshot } from "@/renderers/live2dRenderer";
import { WebLive2DRenderer } from "@/renderers/live2dRenderer";

const props = defineProps<{
  emotion: DisplayEmotion;
  motion: DisplayMotion;
  model: Live2DModelManifest;
  speaking: boolean;
}>();

const live2dHost = ref<HTMLElement | null>(null);
const renderer = ref<WebLive2DRenderer | null>(null);
const loadState = ref<"loading" | "ready" | "fallback">("loading");
const loadError = ref("");
const debugOpen = ref(
  new URLSearchParams(window.location.search).get("debugLive2D") === "1",
);
const debugSnapshot = ref<Live2DDebugSnapshot | null>(null);

const expressionLabel = computed(() => {
  const mapped = props.model.emotionMap[props.emotion];
  return mapped?.[0] ?? "neutral";
});

const motionLabel = computed(() => {
  const mapped = props.model.motionMap[props.motion];
  return mapped ? `${mapped.group} #${mapped.index}` : "Idle #0";
});

async function loadLive2D() {
  if (!live2dHost.value) return;
  loadState.value = "loading";
  loadError.value = "";
  debugSnapshot.value = null;
  renderer.value?.dispose();
  renderer.value = null;
  try {
    const live2dRenderer = new WebLive2DRenderer(live2dHost.value);
    renderer.value = live2dRenderer;
    await live2dRenderer.loadModel(props.model);
    await live2dRenderer.setExpression(props.emotion);
    live2dRenderer.setFpsMode(props.speaking ? "speaking" : "idle");
    loadState.value = "ready";
    refreshDebugSnapshot();
  } catch (error) {
    loadState.value = "fallback";
    loadError.value = error instanceof Error ? error.message : String(error);
    renderer.value?.dispose();
    renderer.value = null;
    debugSnapshot.value = null;
  }
}

function refreshDebugSnapshot() {
  debugSnapshot.value = renderer.value?.getDebugSnapshot() ?? null;
}

function toggleDebugPanel() {
  debugOpen.value = !debugOpen.value;
  if (debugOpen.value) {
    refreshDebugSnapshot();
  }
}

function setDebugPartOpacity(payload: { index: number; opacity: number }) {
  renderer.value?.setDebugPartOpacity(payload.index, payload.opacity);
  refreshDebugSnapshot();
}

function setDebugParameterValue(payload: { index: number; value: number }) {
  renderer.value?.setDebugParameterValue(payload.index, payload.value);
  refreshDebugSnapshot();
}

function resetDebugPartOpacity(index: number) {
  renderer.value?.resetDebugPartOpacity(index);
  refreshDebugSnapshot();
}

function resetDebugParameterValue(index: number) {
  renderer.value?.resetDebugParameterValue(index);
  refreshDebugSnapshot();
}

function resetDebugOverrides() {
  renderer.value?.resetDebugOverrides();
  refreshDebugSnapshot();
}

function setDebugExpression(name: string) {
  void renderer.value?.setDebugExpression(name).then(refreshDebugSnapshot);
}

function resetDebugExpression() {
  renderer.value?.resetDebugExpression();
  refreshDebugSnapshot();
}

function playDebugMotion(payload: { group: string; index: number }) {
  void renderer.value
    ?.playDebugMotion(payload.group, payload.index)
    .then(refreshDebugSnapshot);
}

watch(
  () => props.model.entry,
  () => void loadLive2D(),
);

watch(
  () => props.emotion,
  () => {
    void renderer.value?.setExpression(props.emotion).then(refreshDebugSnapshot);
  },
);

watch(
  () => props.motion,
  () => {
    void renderer.value?.playMotion(props.motion).then(refreshDebugSnapshot);
  },
);

watch(
  () => props.speaking,
  () => {
    renderer.value?.setFpsMode(props.speaking ? "speaking" : "idle");
  },
);

onMounted(() => {
  void loadLive2D();
});

onBeforeUnmount(() => {
  renderer.value?.dispose();
  renderer.value = null;
  debugSnapshot.value = null;
});
</script>

<template>
  <section class="stage" aria-label="Live2D preview stage">
    <div class="stage__status">
      <span>{{ props.model.name }}</span>
      <span>{{ expressionLabel }}</span>
      <span>{{ motionLabel }}</span>
    </div>

    <div ref="live2dHost" class="live2d-host" :data-state="loadState"></div>

    <button
      type="button"
      class="stage__debug-toggle"
      :disabled="loadState !== 'ready'"
      @click="toggleDebugPanel"
    >
      Debug
    </button>

    <Live2DDebugPanel
      v-if="debugOpen"
      :snapshot="debugSnapshot"
      @close="debugOpen = false"
      @refresh="refreshDebugSnapshot"
      @reset-all="resetDebugOverrides"
      @set-part-opacity="setDebugPartOpacity"
      @reset-part-opacity="resetDebugPartOpacity"
      @set-parameter-value="setDebugParameterValue"
      @reset-parameter-value="resetDebugParameterValue"
      @set-expression="setDebugExpression"
      @reset-expression="resetDebugExpression"
      @play-motion="playDebugMotion"
    />

    <div
      v-if="loadState !== 'ready'"
      class="avatar"
      :data-emotion="props.emotion"
      :data-motion="props.motion"
    >
      <div class="avatar__halo"></div>
      <div class="avatar__body">
        <div class="avatar__leaf avatar__leaf--left"></div>
        <div class="avatar__leaf avatar__leaf--right"></div>
        <div class="avatar__face">
          <span class="avatar__eye"></span>
          <span class="avatar__eye"></span>
          <span class="avatar__mouth" :data-speaking="props.speaking"></span>
        </div>
      </div>
      <div class="avatar__shadow"></div>
    </div>

    <div v-if="loadState !== 'ready'" class="stage__error">
      <strong>{{ loadState === "loading" ? "Loading Live2D" : "Live2D fallback" }}</strong>
      <span>{{ loadError || props.model.entry }}</span>
    </div>

    <div class="stage__caption">
      <strong>{{ props.emotion }}</strong>
      <span>{{ props.speaking ? "TTS speaking" : "idle" }}</span>
    </div>
  </section>
</template>
