<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import MotionFeedbackPanel from "@/components/MotionFeedbackPanel.vue";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import {
  mergeRecentMotionPlaybacks,
  readRecentMotionPlaybacks,
} from "@/services/motionPlaybackHistory";

const props = defineProps<{
  recent: MotionPlaybackSummary[];
  feedbackEnabled: boolean;
  replayStatus?: string;
}>();

const emit = defineEmits<{
  replay: [playback: MotionPlaybackSummary];
}>();

const persisted = ref<MotionPlaybackSummary[]>([]);
const selectedId = ref("");
const loading = ref(false);
const errorMessage = ref("");

const history = computed(() =>
  mergeRecentMotionPlaybacks(persisted.value, props.recent, 20),
);
const selected = computed(
  () =>
    history.value.find((playback) => playback.motionPlanId === selectedId.value) ??
    history.value[0] ??
    null,
);
const selectedIndex = computed(() =>
  selected.value
    ? history.value.findIndex(
        (playback) => playback.motionPlanId === selected.value?.motionPlanId,
      )
    : -1,
);

function optionLabel(playback: MotionPlaybackSummary): string {
  const time = new Date(playback.timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const text = playback.assistantText.replace(/\s+/gu, " ").trim();
  return `${time} · ${playback.primitive} · ${text.slice(0, 54)}`;
}

async function refresh(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    persisted.value = await readRecentMotionPlaybacks(20);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    loading.value = false;
  }
}

function selectOffset(offset: number): void {
  const next = history.value[selectedIndex.value + offset];
  if (next) selectedId.value = next.motionPlanId;
}

function replay(): void {
  if (selected.value) emit("replay", selected.value);
}

watch(
  () => history.value.map((playback) => playback.motionPlanId).join("|"),
  () => {
    if (!selectedId.value && history.value[0]) {
      selectedId.value = history.value[0].motionPlanId;
    }
  },
  { immediate: true },
);

onMounted(() => void refresh());
</script>

<template>
  <section class="panel motion-history" aria-label="Recent motion history">
    <header class="panel__header">
      <h2>Recent Motion Replay</h2>
      <button type="button" :disabled="loading" @click="refresh">
        Refresh
      </button>
    </header>

    <div class="motion-history__body">
      <label>
        <span>Recent playback</span>
        <select v-model="selectedId" :disabled="!history.length">
          <option v-if="!history.length" value="">No recorded motions yet</option>
          <option
            v-for="playback in history"
            :key="playback.motionPlanId"
            :value="playback.motionPlanId"
          >
            {{ optionLabel(playback) }}
          </option>
        </select>
      </label>

      <div class="motion-history__controls">
        <button
          type="button"
          :disabled="selectedIndex < 0 || selectedIndex >= history.length - 1"
          @click="selectOffset(1)"
        >
          Older
        </button>
        <button
          type="button"
          :disabled="selectedIndex <= 0"
          @click="selectOffset(-1)"
        >
          Newer
        </button>
        <button type="button" :disabled="!selected" @click="replay">
          Replay selected
        </button>
      </div>

      <dl v-if="selected" class="motion-history__summary">
        <div><dt>Intent</dt><dd>{{ selected.intent.intent }}</dd></div>
        <div><dt>Primitive</dt><dd>{{ selected.primitive }}</dd></div>
        <div><dt>Surface</dt><dd>{{ selected.surface }}</dd></div>
        <div><dt>Result</dt><dd>{{ selected.validationStatus }}</dd></div>
      </dl>

      <p v-if="props.replayStatus" class="motion-history__status" aria-live="polite">
        {{ props.replayStatus }}
      </p>
      <p v-if="errorMessage" class="motion-history__error" role="alert">
        {{ errorMessage }}
      </p>

      <MotionFeedbackPanel
        :playback="selected"
        :enabled="props.feedbackEnabled"
      />
    </div>
  </section>
</template>
