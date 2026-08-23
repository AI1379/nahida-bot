<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";

import type { PomodoroState } from "@/services/pomodoroService";

const props = defineProps<{
  state: PomodoroState;
}>();

const running = computed(() => props.state.phase !== "idle");

const phaseLabel = computed(() => {
  if (props.state.phase === "breaking") return "On break";
  return "Working";
});

const roundLabel = computed(() => {
  const { round, totalRounds } = props.state;
  if (round <= 0 || totalRounds <= 0) return "";
  return `${round}/${totalRounds}`;
});

// The snapshot only refreshes on phase transitions, so the countdown is
// re-derived every second from the `expiresAt` deadline.
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
</script>

<template>
  <div
    v-if="running"
    :key="props.state.phase"
    class="pomodoro-badge"
    :data-phase="props.state.phase"
  >
    <span class="pomodoro-badge__dot" />
    <span class="pomodoro-badge__phase">{{ phaseLabel }}</span>
    <span class="pomodoro-badge__countdown">{{ countdownLabel }}</span>
    <span v-if="roundLabel" class="pomodoro-badge__round">
      {{ roundLabel }}
    </span>
  </div>
</template>
