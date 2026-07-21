<script setup lang="ts">
import type { PomodoroSettings } from "@/domain/config";

const props = defineProps<{
  settings: PomodoroSettings;
}>();

const emit = defineEmits<{
  update: [settings: PomodoroSettings];
  start: [];
  stop: [];
}>();

function update(patch: Partial<PomodoroSettings>) {
  emit("update", {
    ...props.settings,
    ...patch,
  });
}

function toggle() {
  if (props.settings.enabled) {
    emit("stop");
  } else {
    emit("start");
    update({ enabled: true });
  }
}

function changeNumber(
  key: "workDurationMinutes" | "breakDurationMinutes",
  event: Event,
) {
  update({ [key]: Number((event.target as HTMLInputElement).value) });
}

function changeText(
  key: "workStartText" | "breakStartText" | "breakEndText",
  event: Event,
) {
  update({ [key]: (event.target as HTMLInputElement).value });
}
</script>

<template>
  <details class="pomodoro-panel" open>
    <summary class="panel-heading">
      <span class="heading-label">Pomodoro</span>
      <button
        class="toggle-button"
        type="button"
        :aria-pressed="props.settings.enabled"
        @click.prevent="toggle"
      >
        {{ props.settings.enabled ? "Stop" : "Start" }}
      </button>
    </summary>

    <div class="pomodoro-fields" role="group" aria-label="Pomodoro timer settings">
      <label class="field">
        <span class="field-label">Work (minutes)</span>
        <input
          class="field-input"
          type="number"
          :min="1"
          :max="120"
          :value="props.settings.workDurationMinutes"
          @change="(e) => changeNumber('workDurationMinutes', e)"
        />
      </label>
      <label class="field">
        <span class="field-label">Break (minutes)</span>
        <input
          class="field-input"
          type="number"
          :min="1"
          :max="60"
          :value="props.settings.breakDurationMinutes"
          @change="(e) => changeNumber('breakDurationMinutes', e)"
        />
      </label>
      <label class="field">
        <span class="field-label">Work start text</span>
        <input
          class="field-input"
          type="text"
          :value="props.settings.workStartText"
          maxlength="200"
          @change="(e) => changeText('workStartText', e)"
        />
      </label>
      <label class="field">
        <span class="field-label">Break start text</span>
        <input
          class="field-input"
          type="text"
          :value="props.settings.breakStartText"
          maxlength="200"
          @change="(e) => changeText('breakStartText', e)"
        />
      </label>
      <label class="field">
        <span class="field-label">Break end text</span>
        <input
          class="field-input"
          type="text"
          :value="props.settings.breakEndText"
          maxlength="200"
          @change="(e) => changeText('breakEndText', e)"
        />
      </label>
    </div>
  </details>
</template>
