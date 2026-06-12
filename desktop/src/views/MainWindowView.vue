<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import type { UnlistenFn } from "@tauri-apps/api/event";

import {
  desktopWindowDefaults,
} from "@/config/desktopRuntimeDefaults";
import type {
  DesktopRuntimeSnapshot,
  PetWindowCommand,
} from "@/domain/desktopWindowProtocol";
import { petRuntimeNeedsEmerge } from "@/domain/petRuntimeMachine";
import {
  listenForPetCommands,
  publishRuntimeSnapshot,
} from "@/services/desktopWindowBridge";
import { SpeechPlaybackCoordinator } from "@/services/speechPlaybackCoordinator";
import { SystemSpeechAdapter } from "@/services/systemSpeechAdapter";
import { useDesktopStore } from "@/stores/desktop";
import PetRuntimeView from "@/views/PetRuntimeView.vue";
import WorkbenchView from "@/views/WorkbenchView.vue";

const store = useDesktopStore();
const activeView = ref<"runtime" | "workbench">("runtime");

const title = computed(() =>
  activeView.value === "runtime" ? "Pet Runtime" : "Development Workbench",
);

const runtimeSnapshot = computed<DesktopRuntimeSnapshot>(() => ({
  connected: store.connected,
  sessionId: store.sessionId,
  activePlan: store.activePlan,
  activePresentation: store.activePresentation,
  petRuntime: store.petRuntime,
  localConfig: store.localConfig,
  localConfigVersion: store.localConfigVersion,
  expressionMapVersion: store.expressionMapVersion,
  motionMapVersion: store.motionMapVersion,
}));

let transitionTimer: ReturnType<typeof setTimeout> | null = null;
let autoRetreatTimer: ReturnType<typeof setTimeout> | null = null;
let unlistenPetCommands: UnlistenFn | null = null;
let scheduledPresentationId: string | null = null;

const speechPlayback = new SpeechPlaybackCoordinator(
  new SystemSpeechAdapter(
    undefined,
    undefined,
    () => store.localConfig.ttsSettings,
  ),
  {
    onSegmentStart(presentation, index, _segment, mode) {
      if (store.activePresentation?.id !== presentation.id) return;
      store.setSegment(index, mode === "audio");
    },
    onSegmentFallback(presentation, index) {
      if (store.activePresentation?.id !== presentation.id) return;
      store.setSegment(index, false);
    },
    onPresentationComplete(presentation) {
      if (store.activePresentation?.id !== presentation.id) return;
      store.finishPresentation();
    },
  },
);

function clearTimer(timer: ReturnType<typeof setTimeout> | null) {
  if (timer !== null) clearTimeout(timer);
}

function clearPetStateTimers() {
  clearTimer(transitionTimer);
  clearTimer(autoRetreatTimer);
  transitionTimer = null;
  autoRetreatTimer = null;
}

function scheduleActivePresentation() {
  const presentation = store.activePresentation;
  if (!presentation) {
    scheduledPresentationId = null;
    speechPlayback.stop();
    return;
  }
  if (petRuntimeNeedsEmerge(store.petRuntime.status)) {
    if (
      store.petRuntime.status === "retreating" ||
      store.petRuntime.status === "error"
    ) {
      speechPlayback.stop();
    }
    return;
  }
  if (scheduledPresentationId === presentation.id) return;
  scheduledPresentationId = presentation.id;
  speechPlayback.play(presentation);
}

function schedulePetState(status: typeof store.petRuntime.status) {
  clearPetStateTimers();

  // Fallback only: the pet window normally reports `transition_done`
  // when its slide animation settles; without it (browser dev mode)
  // these timers keep the state machine moving.
  if (status === "emerging") {
    transitionTimer = setTimeout(
      () => store.completePetEmerge(),
      desktopWindowDefaults.transitionFallbackMs,
    );
    return;
  }

  if (status === "retreating") {
    transitionTimer = setTimeout(
      () => store.completePetRetreat(),
      desktopWindowDefaults.transitionFallbackMs,
    );
    return;
  }

  if (status === "emerged") {
    autoRetreatTimer = setTimeout(
      () => store.requestPetRetreat(),
      desktopWindowDefaults.autoRetreatMs,
    );
    return;
  }

  if (status === "error") {
    autoRetreatTimer = setTimeout(
      () => store.requestPetRetreat(),
      desktopWindowDefaults.errorRetreatMs,
    );
    return;
  }

  if (status === "chat") {
    autoRetreatTimer = setTimeout(
      () => store.exitPetChat(),
      desktopWindowDefaults.chatIdleTimeoutMs,
    );
  }
}

function handlePetCommand(command: PetWindowCommand) {
  switch (command.type) {
    case "request_state":
      void publishRuntimeSnapshot(runtimeSnapshot.value);
      break;
    case "peek":
      store.requestPetPeek();
      break;
    case "emerge":
      store.requestPetEmerge();
      break;
    case "retreat":
      store.requestPetRetreat();
      break;
    case "hide":
      store.requestPetHide();
      break;
    case "enter_chat":
      store.enterPetChat();
      break;
    case "exit_chat":
      store.exitPetChat();
      break;
    case "submit_message":
      store.submitUserMessage(command.text);
      break;
    case "pointer_activity":
      schedulePetState(store.petRuntime.status);
      break;
    case "transition_done":
      if (command.phase === "emerge") {
        store.completePetEmerge();
      } else {
        store.completePetRetreat();
      }
      break;
  }
}

function selectModel(event: Event) {
  const target = event.target as HTMLSelectElement;
  store.selectModel(target.value);
}

watch(
  () =>
    [
      store.activePresentation?.id ?? null,
      store.petRuntime.status,
    ] as const,
  () => scheduleActivePresentation(),
  { immediate: true },
);

watch(
  // lastEventAt re-arms the timers when activity happens without a status
  // change (e.g. a reply playing inside chat keeps the chat session alive).
  () => [store.petRuntime.status, store.petRuntime.lastEventAt] as const,
  ([status]) => schedulePetState(status),
  { immediate: true },
);

watch(
  runtimeSnapshot,
  (snapshot) => {
    void publishRuntimeSnapshot(snapshot);
  },
  { deep: true, immediate: true },
);

onMounted(async () => {
  unlistenPetCommands = await listenForPetCommands(handlePetCommand);
  store.startMockBackend();
});

onBeforeUnmount(() => {
  speechPlayback.dispose();
  clearPetStateTimers();
  unlistenPetCommands?.();
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
        <div class="pet-controls" aria-label="Pet window controls">
          <button type="button" @click="store.requestPetEmerge()">
            Emerge
          </button>
          <button type="button" @click="store.enterPetChat()">Chat</button>
          <button type="button" @click="store.requestPetRetreat()">
            Retreat
          </button>
        </div>
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
