<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";

import type { PomodoroSettings } from "@/domain/config";
import type { PomodoroState } from "@/services/pomodoroService";

const props = defineProps<{
  settings: PomodoroSettings;
  state: PomodoroState;
}>();

const emit = defineEmits<{
  update: [settings: PomodoroSettings];
  start: [];
  stop: [];
}>();

const running = computed(() => props.state.phase !== "idle");

const phaseLabel = computed(() => {
  if (props.state.phase === "working") return "专注中";
  if (props.state.phase === "breaking") return "休息中";
  return "未开始";
});

const roundLabel = computed(() => {
  const { round, totalRounds } = props.state;
  if (round <= 0 || totalRounds <= 0) return "";
  return `${round}/${totalRounds}`;
});

const nowMs = ref(Date.now());
let ticker: ReturnType<typeof setInterval> | null = null;

watch(
  () => props.state.phase,
  (phase) => {
    if (phase === "idle") {
      if (ticker !== null) {
        clearInterval(ticker);
        ticker = null;
      }
      return;
    }
    if (ticker === null) {
      nowMs.value = Date.now();
      ticker = setInterval(() => {
        nowMs.value = Date.now();
      }, 1000);
    }
  },
  { immediate: true },
);

onUnmounted(() => {
  if (ticker !== null) clearInterval(ticker);
});

const countdownLabel = computed(() => {
  if (!props.state.expiresAt) return "--:--";
  const remaining = Math.max(
    0,
    Math.round(
      (new Date(props.state.expiresAt).getTime() - nowMs.value) / 1000,
    ),
  );
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
});

function update(patch: Partial<PomodoroSettings>) {
  emit("update", {
    ...props.settings,
    ...patch,
  });
}

function toggleEnabled() {
  const enabled = !props.settings.enabled;
  update({ enabled });
  if (!enabled && running.value) emit("stop");
}

function toggleRun() {
  if (running.value) {
    emit("stop");
  } else {
    emit("start");
  }
}

function changeNumber(
  key:
    | "workDurationMinutes"
    | "breakDurationMinutes"
    | "totalRounds",
  event: Event,
) {
  update({ [key]: Number((event.target as HTMLInputElement).value) });
}

function changeText(
  key:
    | "workStartText"
    | "breakStartText"
    | "breakEndText"
    | "roundsDoneText"
    | "dynamicTextModel",
  event: Event,
) {
  update({ [key]: (event.target as HTMLInputElement).value });
}
</script>

<template>
  <section class="panel pomodoro-settings" aria-label="番茄钟设置">
    <header class="panel__header">
      <h2>专注计时</h2>
      <span>本地番茄钟插件</span>
    </header>

    <div class="pomodoro-settings__body">
      <div class="pomodoro-settings__status" :data-phase="props.state.phase">
        <span class="pomodoro-settings__phase">
          {{ phaseLabel }}<template v-if="roundLabel"> · {{ roundLabel }}</template>
        </span>
        <span v-if="running" class="pomodoro-settings__countdown">{{
          countdownLabel
        }}</span>
      </div>

      <div class="pomodoro-settings__controls">
        <button
          class="settings-button"
          :class="running ? '' : 'settings-button--primary'"
          type="button"
          :disabled="!running && !props.settings.enabled"
          @click="toggleRun"
        >
          {{ running ? "停止" : "开始" }}
        </button>
        <div class="pomodoro-settings__switches">
          <label class="pomodoro-settings__check">
            <input
              type="checkbox"
              :checked="props.settings.enabled"
              @change="toggleEnabled"
            />
            <span>启用提醒</span>
          </label>
          <label class="pomodoro-settings__check">
            <input
              type="checkbox"
              :checked="props.settings.speakReminders"
              @change="update({ speakReminders: !props.settings.speakReminders })"
            />
            <span>朗读提醒（TTS）</span>
          </label>
          <label class="pomodoro-settings__check">
            <input
              type="checkbox"
              :checked="props.settings.dynamicText"
              @change="update({ dynamicText: !props.settings.dynamicText })"
            />
            <span>动态文案（Gateway 模型）</span>
          </label>
        </div>
      </div>

      <label v-if="props.settings.dynamicText" class="pomodoro-settings__field">
        <span>动态文案模型</span>
        <input
          type="text"
          :value="props.settings.dynamicTextModel"
          maxlength="128"
          placeholder="primary / cheap / provider-model；留空使用 Gateway 默认值"
          @change="(e) => changeText('dynamicTextModel', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>专注时长（分钟）</span>
        <input
          type="number"
          min="1"
          max="120"
          :value="props.settings.workDurationMinutes"
          @change="(e) => changeNumber('workDurationMinutes', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>休息时长（分钟）</span>
        <input
          type="number"
          min="1"
          max="60"
          :value="props.settings.breakDurationMinutes"
          @change="(e) => changeNumber('breakDurationMinutes', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>轮数</span>
        <input
          type="number"
          min="1"
          max="16"
          :value="props.settings.totalRounds"
          @change="(e) => changeNumber('totalRounds', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>开始专注文案</span>
        <input
          type="text"
          :value="props.settings.workStartText"
          maxlength="200"
          @change="(e) => changeText('workStartText', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>开始休息文案</span>
        <input
          type="text"
          :value="props.settings.breakStartText"
          maxlength="200"
          @change="(e) => changeText('breakStartText', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>休息结束文案（仍有下一轮）</span>
        <input
          type="text"
          :value="props.settings.breakEndText"
          maxlength="200"
          @change="(e) => changeText('breakEndText', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>全部完成文案</span>
        <input
          type="text"
          :value="props.settings.roundsDoneText"
          maxlength="200"
          @change="(e) => changeText('roundsDoneText', e)"
        />
      </label>

      <p class="pomodoro-settings__note">
        开始后会按设定轮数交替执行专注和休息，并在最后一轮后自动停止。
        阶段切换时，桌宠会弹出气泡提醒；运行中关闭提醒也会停止计时。
        动态文案会提前由 Gateway 模型生成，离线时自动使用下方固定文案。
        模型可填写 Gateway 标签（如 primary、cheap）或固定 provider/model，留空则使用默认模型。
      </p>
    </div>
  </section>
</template>
