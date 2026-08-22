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
  if (props.state.phase === "working") return "Working";
  if (props.state.phase === "breaking") return "On break";
  return "Idle";
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
    | "roundsDoneText",
  event: Event,
) {
  update({ [key]: (event.target as HTMLInputElement).value });
}
</script>

<template>
  <section class="panel pomodoro-settings" aria-label="Pomodoro timer">
    <header class="panel__header">
      <h2>Pomodoro</h2>
      <span>local focus timer</span>
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
          {{ running ? "Stop" : "Start" }}
        </button>
        <div class="pomodoro-settings__switches">
          <label class="pomodoro-settings__check">
            <input
              type="checkbox"
              :checked="props.settings.enabled"
              @change="toggleEnabled"
            />
            <span>Reminders enabled</span>
          </label>
          <label class="pomodoro-settings__check">
            <input
              type="checkbox"
              :checked="props.settings.speakReminders"
              @change="update({ speakReminders: !props.settings.speakReminders })"
            />
            <span>Speak reminders (TTS)</span>
          </label>
          <label class="pomodoro-settings__check">
            <input
              type="checkbox"
              :checked="props.settings.dynamicText"
              @change="update({ dynamicText: !props.settings.dynamicText })"
            />
            <span>Dynamic text (Gateway model)</span>
          </label>
        </div>
      </div>

      <label class="pomodoro-settings__field">
        <span>Work (minutes)</span>
        <input
          type="number"
          min="1"
          max="120"
          :value="props.settings.workDurationMinutes"
          @change="(e) => changeNumber('workDurationMinutes', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>Break (minutes)</span>
        <input
          type="number"
          min="1"
          max="60"
          :value="props.settings.breakDurationMinutes"
          @change="(e) => changeNumber('breakDurationMinutes', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>Rounds</span>
        <input
          type="number"
          min="1"
          max="16"
          :value="props.settings.totalRounds"
          @change="(e) => changeNumber('totalRounds', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>Work start text</span>
        <input
          type="text"
          :value="props.settings.workStartText"
          maxlength="200"
          @change="(e) => changeText('workStartText', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>Break start text</span>
        <input
          type="text"
          :value="props.settings.breakStartText"
          maxlength="200"
          @change="(e) => changeText('breakStartText', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>Break end text (more rounds)</span>
        <input
          type="text"
          :value="props.settings.breakEndText"
          maxlength="200"
          @change="(e) => changeText('breakEndText', e)"
        />
      </label>

      <label class="pomodoro-settings__field">
        <span>All rounds done text</span>
        <input
          type="text"
          :value="props.settings.roundsDoneText"
          maxlength="200"
          @change="(e) => changeText('roundsDoneText', e)"
        />
      </label>

      <p class="pomodoro-settings__note">
        Start runs the configured number of work+break rounds and stops by
        itself after the last one. The reminder switch controls whether phase
        changes pop the pet out with a bubble; turning it off while running
        also stops the timer. Dynamic text lets the Gateway model write each
        reminder line ahead of time and falls back to the texts below when
        offline.
      </p>
    </div>
  </section>
</template>
