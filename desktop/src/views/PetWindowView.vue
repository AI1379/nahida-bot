<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { UnlistenFn } from "@tauri-apps/api/event";

import Live2DStage from "@/components/Live2DStage.vue";
import type { ProximityIntent } from "@/domain/petProximity";
import {
  listenForRuntimeSnapshots,
  sendPetWindowCommand,
} from "@/services/desktopWindowBridge";
import { PetProximityWatcher } from "@/services/petProximityWatcher";
import { PetWindowController } from "@/services/petWindowController";
import { useDesktopStore } from "@/stores/desktop";

const store = useDesktopStore();
const replyText = ref("");
const windowController = new PetWindowController();
const proximityWatcher = new PetProximityWatcher();
const interactive = computed(
  () => store.petRuntime.interactionMode === "interactive",
);
let unlistenRuntimeSnapshots: UnlistenFn | null = null;
let snapshotReceived = false;
const stateRequestTimers: Array<ReturnType<typeof setTimeout>> = [];

windowController.onSlideSettled = (phase) => {
  void sendPetWindowCommand({ type: "transition_done", phase });
};

function queueWindowUpdate() {
  windowController.schedule(
    store.petRuntime,
    store.localConfig.windowState,
  );
}

function handleProximityIntent(intent: ProximityIntent) {
  switch (intent) {
    case "peek":
      void sendPetWindowCommand({ type: "peek" });
      break;
    case "emerge":
      void sendPetWindowCommand({ type: "emerge" });
      break;
    case "hide":
      void sendPetWindowCommand({ type: "hide" });
      break;
    case "activity":
      void sendPetWindowCommand({ type: "pointer_activity" });
      break;
  }
}

function submitReply() {
  const trimmed = replyText.value.trim();
  if (!trimmed) return;
  void sendPetWindowCommand({ type: "submit_message", text: trimmed });
  replyText.value = "";
}

watch(
  () => [
    store.petRuntime.status,
    store.petRuntime.clickThrough,
    store.petRuntime.interactionMode,
    store.localConfig.windowState.width,
    store.localConfig.windowState.height,
    store.localConfig.windowState.edge,
    store.localConfig.windowState.exposedPx,
  ] as const,
  () => queueWindowUpdate(),
  { immediate: true },
);

onMounted(async () => {
  unlistenRuntimeSnapshots = await listenForRuntimeSnapshots((snapshot) => {
    snapshotReceived = true;
    store.applyRuntimeSnapshot(snapshot);
  });
  await sendPetWindowCommand({ type: "request_state" });
  for (const delay of [250, 1000]) {
    stateRequestTimers.push(
      setTimeout(() => {
        if (!snapshotReceived) {
          void sendPetWindowCommand({ type: "request_state" });
        }
      }, delay),
    );
  }
  proximityWatcher.start(
    () => store.petRuntime.status,
    handleProximityIntent,
  );
});

onBeforeUnmount(() => {
  windowController.dispose();
  proximityWatcher.stop();
  for (const timer of stateRequestTimers) clearTimeout(timer);
  unlistenRuntimeSnapshots?.();
});
</script>

<template>
  <main
    class="pet-window"
    :data-status="store.petRuntime.status"
    :data-interactive="interactive"
  >
    <Live2DStage
      :emotion="store.currentEmotion"
      :expression-key="store.currentExpressionKey"
      :motion="store.currentMotion"
      :render-mode="store.petRuntime.renderMode"
      :model="store.model"
      :speaking="store.speaking"
      :caption-text="store.petRuntime.bubbleText"
      :expression-map-version="store.expressionMapVersion"
      :motion-map-version="store.motionMapVersion"
      :debug-enabled="false"
      :dev-chrome="false"
    />

    <form
      v-if="interactive"
      class="pet-window__composer"
      @submit.prevent="submitReply"
    >
      <input
        v-model="replyText"
        type="text"
        :disabled="!store.connected"
        placeholder="Reply"
        autofocus
      />
      <button
        type="submit"
        :disabled="!store.connected || !replyText.trim()"
      >
        Send
      </button>
      <button
        type="button"
        aria-label="Close chat"
        @click="sendPetWindowCommand({ type: 'exit_chat' })"
      >
        Close
      </button>
    </form>
  </main>
</template>
