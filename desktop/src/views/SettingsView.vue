<script setup lang="ts">
import { ref } from "vue";
import TtsSettingsPanel from "@/components/TtsSettingsPanel.vue";
import GatewayConnectionPanel from "@/components/GatewayConnectionPanel.vue";
import PomodoroSettingsPanel from "@/components/PomodoroSettingsPanel.vue";
import RemoteControlSettingsPanel from "@/components/RemoteControlSettingsPanel.vue";
import PetTriggerSettingsPanel from "@/components/PetTriggerSettingsPanel.vue";
import MotionDataPanel from "@/components/MotionDataPanel.vue";
import DesktopPetSettingsPanel from "@/components/DesktopPetSettingsPanel.vue";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();

type SettingsSection =
  | "connection"
  | "pet"
  | "voice"
  | "focus"
  | "security"
  | "advanced";

const activeSection = ref<SettingsSection>("connection");
const sections: Array<{ id: SettingsSection; label: string; hint: string }> = [
  { id: "connection", label: "连接", hint: "Gateway 与设备配对" },
  { id: "pet", label: "桌宠", hint: "模型、位置和触发方式" },
  { id: "voice", label: "语音", hint: "系统语音和试听" },
  { id: "focus", label: "专注", hint: "番茄钟与提醒" },
  { id: "security", label: "安全", hint: "本机远程访问边界" },
  { id: "advanced", label: "高级", hint: "动作数据与诊断" },
];

function updateTtsSettings(next: typeof store.localConfig.ttsSettings) {
  store.updateTtsSettings(next);
}

function updatePomodoroSettings(next: typeof store.localConfig.pomodoro) {
  store.updatePomodoroSettings(next);
}

function updateMotionDataCollectionEnabled(enabled: boolean) {
  store.updateMotionDataCollectionEnabled(enabled);
}

function updatePetTriggerSettings(next: typeof store.localConfig.petTriggers) {
  store.updatePetTriggerSettings(next);
}
</script>

<template>
  <section class="settings-view" aria-label="桌面端设置">
    <header class="settings-view__intro">
      <div>
        <p class="settings-view__eyebrow">桌面偏好设置</p>
        <p class="settings-view__description">
          管理 Gateway 连接、桌宠表现、语音播放与这台设备的本地访问边界。
        </p>
      </div>
      <span class="settings-view__privacy">保存在本机</span>
    </header>

    <p v-if="store.persistenceError" class="settings-view__error" role="alert">
      {{ store.persistenceError }}
    </p>

    <div class="settings-view__layout">
      <nav class="settings-view__nav" aria-label="设置分类">
        <button
          v-for="section in sections"
          :key="section.id"
          type="button"
          :class="{ 'is-active': activeSection === section.id }"
          :aria-current="activeSection === section.id ? 'page' : undefined"
          @click="activeSection = section.id"
        >
          <strong>{{ section.label }}</strong>
          <span>{{ section.hint }}</span>
        </button>
      </nav>

      <div class="settings-view__content">
        <GatewayConnectionPanel
          v-if="activeSection === 'connection'"
          :runtime="props.runtime"
        />

        <template v-else-if="activeSection === 'pet'">
          <DesktopPetSettingsPanel />
          <PetTriggerSettingsPanel
            :settings="store.localConfig.petTriggers"
            @update="updatePetTriggerSettings"
          />
        </template>

        <TtsSettingsPanel
          v-else-if="activeSection === 'voice'"
          :settings="store.localConfig.ttsSettings"
          @update="updateTtsSettings"
          @preview="store.previewSystemSpeech"
        />

        <PomodoroSettingsPanel
          v-else-if="activeSection === 'focus'"
          :settings="store.localConfig.pomodoro"
          :state="store.pomodoroState"
          @update="updatePomodoroSettings"
          @start="props.runtime.startPomodoro"
          @stop="props.runtime.stopPomodoro"
        />

        <RemoteControlSettingsPanel v-else-if="activeSection === 'security'" />

        <MotionDataPanel
          v-else
          :enabled="store.localConfig.motionDataCollectionEnabled"
          @update-enabled="updateMotionDataCollectionEnabled"
        />
      </div>
    </div>
  </section>
</template>
