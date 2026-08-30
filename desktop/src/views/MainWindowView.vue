<script setup lang="ts">
import {
  computed,
  defineAsyncComponent,
  onBeforeUnmount,
  onMounted,
  ref,
} from "vue";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { useDesktopRuntimeController } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";
import OnboardingView from "@/views/OnboardingView.vue";
import PetRuntimeView from "@/views/PetRuntimeView.vue";
import SettingsView from "@/views/SettingsView.vue";
import { listenForMotionPlaybacks } from "@/services/desktopWindowBridge";
import { readRecentMotionPlaybacks } from "@/services/motionPlaybackHistory";

const WorkbenchView = defineAsyncComponent(
  () => import("@/views/WorkbenchView.vue"),
);

const store = useDesktopStore();
const runtime = useDesktopRuntimeController(store);

type DesktopView = "runtime" | "workbench" | "settings";
const activeView = ref<DesktopView>("runtime");
const developerToolsAvailable = import.meta.env.DEV;
let unlistenMotionPlaybacks: UnlistenFn | null = null;

const title = computed(() => {
  switch (activeView.value) {
    case "workbench":
      return "开发工具";
    case "settings":
      return "设置";
    default:
      return "桌面助手";
  }
});

const needsOnboarding = computed(
  () =>
    !store.connected &&
    store.gatewayConnection.mode === "gateway" &&
    !store.gatewayConnection.nodeToken,
);

const petIsRetracted = computed(() =>
  ["hidden", "retreating"].includes(store.petRuntime.status),
);

const petStatusLabel = computed(() => {
  switch (store.petRuntime.status) {
    case "hidden":
    case "retreating":
      return "桌宠已收起";
    case "peek":
    case "emerging":
      return "桌宠正在出现";
    case "chat":
      return "桌宠对话中";
    case "speaking":
      return "桌宠播报中";
    case "error":
      return "桌宠状态异常";
    default:
      return "桌宠已显示";
  }
});

const connectionStatusLabel = computed(() => {
  if (store.connected) {
    return store.gatewayConnection.mode === "mock"
      ? "离线体验"
      : "Gateway 已连接";
  }
  if (store.gatewayConnectionStatus === "connecting") return "正在连接";
  if (store.gatewayConnectionStatus === "auth-required") return "需要配对";
  if (store.gatewayConnectionError) return "连接异常";
  return "未连接";
});

const connectionStatusState = computed(() => {
  if (store.connected) return "connected";
  if (store.gatewayConnectionStatus === "connecting") return "connecting";
  if (store.gatewayConnectionError) return "error";
  return "offline";
});

function togglePet() {
  if (petIsRetracted.value) {
    store.requestPetEmerge();
  } else {
    store.requestPetRetreat();
  }
}

onMounted(async () => {
  unlistenMotionPlaybacks = await listenForMotionPlaybacks((playback) => {
    store.rememberMotionPlayback(playback);
  });
  try {
    store.mergeRecentMotionPlaybackHistory(await readRecentMotionPlaybacks());
  } catch {
    // Feedback remains available for new in-session playbacks.
  }
});

onBeforeUnmount(() => {
  unlistenMotionPlaybacks?.();
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
        <div class="pet-controls" aria-label="桌宠窗口控制">
          <button type="button" @click="togglePet">
            {{ petIsRetracted ? "唤出桌宠" : "收起桌宠" }}
          </button>
          <button type="button" @click="store.enterPetChat()">打开对话</button>
        </div>
        <div class="view-switch" role="tablist" aria-label="桌面端页面">
          <button
            type="button"
            role="tab"
            :aria-selected="activeView === 'runtime'"
            :class="{ 'is-active': activeView === 'runtime' }"
            @click="activeView = 'runtime'"
          >
            对话
          </button>
          <button
            v-if="developerToolsAvailable"
            type="button"
            role="tab"
            :aria-selected="activeView === 'workbench'"
            :class="{ 'is-active': activeView === 'workbench' }"
            @click="activeView = 'workbench'"
          >
            开发工具
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="activeView === 'settings'"
            :class="{ 'is-active': activeView === 'settings' }"
            @click="activeView = 'settings'"
          >
            设置
          </button>
        </div>
        <div class="status-cluster" aria-label="桌面端状态">
          <button
            type="button"
            class="status-pill status-pill--button"
            :data-state="connectionStatusState"
            @click="activeView = 'settings'"
          >
            {{ connectionStatusLabel }}
          </button>
          <span class="status-pill" :data-state="store.petRuntime.status">
            {{ petStatusLabel }}
          </span>
        </div>
      </div>
    </section>

    <OnboardingView
      v-if="activeView === 'runtime' && needsOnboarding"
      @open-settings="activeView = 'settings'"
      @use-offline-demo="runtime.connectMockBackend()"
    />
    <PetRuntimeView
      v-else-if="activeView === 'runtime'"
      :runtime="runtime"
    />
    <WorkbenchView v-else-if="activeView === 'workbench'" :runtime="runtime" />
    <SettingsView v-else :runtime="runtime" />
  </main>
</template>
