<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import type { DisplayEmotion, DisplayMotion } from "@/domain/displayPlan";
import type { RenderMode } from "@/domain/runtime";
import type {
  Live2DExpressionOption,
  Live2DModelManifest,
  Live2DMotionOption,
} from "@/domain/live2d";
import { live2dModelLoadKey } from "@/domain/live2d";
import Live2DDebugPanel from "@/components/Live2DDebugPanel.vue";
import type { Live2DDebugSnapshot } from "@/renderers/live2dRenderer";
import { WebLive2DRenderer } from "@/renderers/live2dRenderer";
import { Live2DPresentationController } from "@/services/live2dPresentationController";

const props = withDefaults(defineProps<{
  emotion: DisplayEmotion;
  expressionKey: string;
  motion: DisplayMotion;
  renderMode: RenderMode;
  model: Live2DModelManifest;
  speaking: boolean;
  captionText: string;
  expressionMapVersion: number;
  motionMapVersion: number;
  debugEnabled?: boolean;
  devChrome?: boolean;
}>(), {
  debugEnabled: true,
  devChrome: true,
});

const emit = defineEmits<{
  expressionsLoaded: [expressions: Live2DExpressionOption[]];
  motionsLoaded: [motions: Live2DMotionOption[]];
}>();

const live2dHost = ref<HTMLElement | null>(null);
const renderer = ref<WebLive2DRenderer | null>(null);
const controller = ref<Live2DPresentationController | null>(null);
const loadState = ref<"loading" | "ready" | "fallback">("loading");
const loadError = ref("");
const debugOpen = ref(
  props.debugEnabled &&
    new URLSearchParams(window.location.search).get("debugLive2D") === "1",
);
const debugSnapshot = ref<Live2DDebugSnapshot | null>(null);
let loadGeneration = 0;
let loadQueue = Promise.resolve();

const expressionLabel = computed(() => {
  const mapped =
    props.model.emotionMap[props.expressionKey] ??
    props.model.emotionMap[props.emotion];
  return mapped?.[0] ?? "neutral";
});

const motionLabel = computed(() => {
  const mapped = props.model.motionMap[props.motion];
  if (!mapped) return "Base fallback";
  if (mapped.source === "none") return "none";
  if (mapped.source === "procedural") return `Base ${mapped.motion}`;
  return `${mapped.group} #${mapped.index}`;
});

const modelLoadKey = computed(
  () => live2dModelLoadKey(props.model),
);

function loadLive2D(): Promise<void> {
  if (!live2dHost.value) return Promise.resolve();
  const generation = ++loadGeneration;
  loadState.value = "loading";
  loadError.value = "";
  debugSnapshot.value = null;
  emit("expressionsLoaded", []);
  emit("motionsLoaded", []);
  controller.value?.dispose();
  controller.value = null;
  renderer.value = null;
  const load = loadQueue.then(() => performLive2DLoad(generation));
  loadQueue = load.catch(() => {});
  return load;
}

async function performLive2DLoad(generation: number): Promise<void> {
  if (!live2dHost.value || generation !== loadGeneration) return;
  const live2dRenderer = new WebLive2DRenderer(live2dHost.value);
  const presentationController = new Live2DPresentationController(
    live2dRenderer,
  );
  try {
    await presentationController.loadModel(props.model);
    if (generation !== loadGeneration) {
      presentationController.dispose();
      return;
    }
    await presentationController.applyPresentation({
      expressionKey: props.expressionKey,
      emotion: props.emotion,
      motion: props.motion,
      renderMode: props.renderMode,
    });
    if (generation !== loadGeneration) {
      presentationController.dispose();
      return;
    }
    renderer.value = live2dRenderer;
    controller.value = presentationController;
    if (document.hidden) {
      presentationController.setRenderMode("suspended");
    }
    loadState.value = "ready";
    refreshDebugSnapshot();
  } catch (error) {
    presentationController.dispose();
    if (generation !== loadGeneration) return;
    loadState.value = "fallback";
    loadError.value = error instanceof Error ? error.message : String(error);
    controller.value = null;
    renderer.value = null;
    debugSnapshot.value = null;
  }
}

function refreshDebugSnapshot() {
  const snapshot = renderer.value?.getDebugSnapshot() ?? null;
  debugSnapshot.value = snapshot;
  if (snapshot) {
    emit("expressionsLoaded", snapshot.expressions);
    emit("motionsLoaded", snapshot.motions);
  }
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

function playDebugMotion(payload: {
  source: "model" | "procedural";
  group: string;
  index: number;
  motion?: DisplayMotion;
}) {
  void renderer.value
    ?.playDebugMotion(payload.group, payload.index, payload.source, payload.motion)
    .then(refreshDebugSnapshot);
}

watch(
  modelLoadKey,
  () => void loadLive2D(),
);

watch(
  () => props.model,
  (model) => controller.value?.setManifest(model),
  { deep: true },
);

watch(
  () => [props.expressionKey, props.emotion, props.expressionMapVersion] as const,
  () => {
    void controller.value
      ?.applyExpression(props.expressionKey, props.emotion)
      .then(refreshDebugSnapshot)
      .catch(() => {});
  },
);

watch(
  () => [props.motion, props.motionMapVersion] as const,
  () => {
    void controller.value
      ?.playMotion(props.motion)
      .then(refreshDebugSnapshot)
      .catch(() => {});
  },
);

watch(
  () => props.renderMode,
  () => {
    if (document.hidden) return;
    controller.value?.setRenderMode(props.renderMode);
  },
);

function handleVisibilityChange() {
  // A minimized/hidden window must not keep burning frames (design §9.8).
  controller.value?.setRenderMode(
    document.hidden ? "suspended" : props.renderMode,
  );
}

onMounted(() => {
  document.addEventListener("visibilitychange", handleVisibilityChange);
  void loadLive2D();
});

onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  loadGeneration += 1;
  controller.value?.dispose();
  controller.value = null;
  renderer.value = null;
  debugSnapshot.value = null;
});
</script>

<template>
  <section class="stage" aria-label="Live2D preview stage">
    <div v-if="props.devChrome" class="stage__status">
      <span>{{ props.model.name }}</span>
      <span>{{ props.expressionKey }}</span>
      <span>{{ expressionLabel }}</span>
      <span>{{ motionLabel }}</span>
    </div>

    <div ref="live2dHost" class="live2d-host" :data-state="loadState"></div>

    <button
      v-if="props.debugEnabled"
      type="button"
      class="stage__debug-toggle"
      :disabled="loadState !== 'ready'"
      @click="toggleDebugPanel"
    >
      Debug
    </button>

    <Live2DDebugPanel
      v-if="props.debugEnabled && debugOpen"
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
      v-if="loadState === 'fallback'"
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

    <div v-if="props.devChrome" class="stage__caption">
      <strong>{{ props.emotion }}</strong>
      <span>{{ props.expressionKey }}</span>
      <span>{{ props.speaking ? "TTS speaking" : "idle" }}</span>
    </div>

    <p v-if="props.captionText" class="stage__subtitle">
      {{ props.captionText }}
    </p>
  </section>
</template>
