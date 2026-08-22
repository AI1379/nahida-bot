<script setup lang="ts">
import { computed, ref, watch } from "vue";

import {
  motionEmotions,
  motionIntentNames,
  type MotionEmotion,
  type MotionIntentName,
} from "@/domain/motionIntent";
import type {
  MotionPlaybackSummary,
  MotionPreferenceCorrection,
  MotionPreferenceLabel,
  MotionPreferenceRecord,
} from "@/domain/motionTelemetry";
import {
  LocalMotionPreferenceStore,
  readActiveMotionPreferences,
} from "@/services/motionDatasetStorage";

const props = withDefaults(defineProps<{
  playback: MotionPlaybackSummary | null;
  enabled: boolean;
  compact?: boolean;
  collapsible?: boolean;
  initiallyCollapsed?: boolean;
  replayable?: boolean;
}>(), {
  compact: false,
  collapsible: false,
  initiallyCollapsed: false,
  replayable: false,
});

const emit = defineEmits<{
  replay: [playback: MotionPlaybackSummary];
}>();

const feedbackOptions: Array<{
  value: MotionPreferenceLabel;
  label: string;
}> = [
  { value: "good", label: "Good" },
  { value: "bad", label: "Bad" },
  { value: "too_much", label: "Too much" },
  { value: "too_little", label: "Too little" },
  { value: "wrong_emotion", label: "Wrong emotion" },
  { value: "repetitive", label: "Repetitive" },
];

const preferenceStore = new LocalMotionPreferenceStore();
const currentPreference = ref<MotionPreferenceRecord | null>(null);
const busy = ref(false);
const status = ref("");
const errorMessage = ref("");
const correctionOpen = ref(false);
const correctedIntent = ref<MotionIntentName | "">("");
const correctedEmotion = ref<MotionEmotion | "">("");
const correctedIntensity = ref(0.5);
const collapsed = ref(props.collapsible && props.initiallyCollapsed);

const selectedLabel = computed(
  () => currentPreference.value?.labels[0] ?? null,
);

function uniqueId(prefix: string): string {
  const id = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${id}`;
}

async function loadCurrentPreference(): Promise<void> {
  currentPreference.value = null;
  status.value = "";
  errorMessage.value = "";
  correctionOpen.value = false;
  const playback = props.playback;
  if (!playback) return;
  try {
    const matches = (await readActiveMotionPreferences())
      .filter((record) => record.candidateA === playback.motionPlanId)
      .sort((left, right) => right.timestamp.localeCompare(left.timestamp));
    currentPreference.value = matches[0] ?? null;
    const correction = currentPreference.value?.correction;
    correctedIntent.value = correction?.intent ?? "";
    correctedEmotion.value = correction?.emotion ?? "";
    correctedIntensity.value =
      correction?.intensity ?? playback.intent.intensity;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error);
  }
}

async function retractCurrent(): Promise<void> {
  const motionPlanId = props.playback?.motionPlanId;
  if (!motionPlanId) return;
  const activePreferences = (await readActiveMotionPreferences()).filter(
    (record) => record.candidateA === motionPlanId,
  );
  for (const preference of activePreferences) {
    await preferenceStore.retract({
      schemaVersion: 1,
      type: "motion_preference_retraction",
      timestamp: new Date().toISOString(),
      retractionId: uniqueId("retraction"),
      retractsPreferenceId: preference.preferenceId,
      motionPlanId,
    });
  }
}

async function saveFeedback(
  label: MotionPreferenceLabel,
  correction?: MotionPreferenceCorrection,
): Promise<void> {
  const playback = props.playback;
  if (!playback || !props.enabled || busy.value) return;
  busy.value = true;
  status.value = "";
  errorMessage.value = "";
  try {
    await retractCurrent();
    const record: MotionPreferenceRecord = {
      schemaVersion: 1,
      type: "motion_preference",
      timestamp: new Date().toISOString(),
      preferenceId: uniqueId("preference"),
      assistantText: playback.assistantText,
      candidateA: playback.motionPlanId,
      winner: label === "good" ? playback.motionPlanId : undefined,
      labels: [label],
      correction,
      playbackSurface: playback.surface,
    };
    await preferenceStore.record(record);
    currentPreference.value = record;
    correctionOpen.value = label === "bad" || label === "wrong_emotion";
    status.value = "Saved locally";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

async function saveCorrection(): Promise<void> {
  const label = selectedLabel.value;
  if (!label) return;
  const correction: MotionPreferenceCorrection = {
    intent: correctedIntent.value || undefined,
    emotion: correctedEmotion.value || undefined,
    intensity: correctedIntensity.value,
  };
  await saveFeedback(label, correction);
  correctionOpen.value = false;
}

async function undoFeedback(): Promise<void> {
  if (!currentPreference.value || busy.value) return;
  busy.value = true;
  status.value = "";
  errorMessage.value = "";
  try {
    await retractCurrent();
    currentPreference.value = null;
    correctionOpen.value = false;
    status.value = "Feedback undone";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

watch(
  () => props.playback?.motionPlanId,
  () => void loadCurrentPreference(),
  { immediate: true },
);
</script>

<template>
  <section
    class="motion-feedback"
    :class="{ 'motion-feedback--compact': props.compact }"
    aria-label="Motion feedback"
  >
    <header class="motion-feedback__header">
      <div class="motion-feedback__heading">
        <strong>Rate the last motion</strong>
        <span v-if="props.playback">
          {{ props.playback.primitive }} · {{ props.playback.intent.intent }}
        </span>
        <span v-else>Use the pet normally; the latest reply will appear here.</span>
      </div>
      <div class="motion-feedback__header-actions">
        <button
          v-if="props.replayable && props.playback && !collapsed"
          type="button"
          @click="emit('replay', props.playback)"
        >
          Replay
        </button>
        <button
          v-if="currentPreference && !collapsed"
          type="button"
          :disabled="busy"
          @click="undoFeedback"
        >
          Undo
        </button>
        <button
          v-if="props.collapsible"
          type="button"
          @click="collapsed = !collapsed"
        >
          {{ collapsed ? "Rate" : "Hide" }}
        </button>
      </div>
    </header>

    <p v-if="props.playback && !collapsed" class="motion-feedback__text">
      {{ props.playback.assistantText }}
    </p>

    <div v-if="!collapsed" class="motion-feedback__buttons">
      <button
        v-for="option in feedbackOptions"
        :key="option.value"
        type="button"
        :class="{ 'is-selected': selectedLabel === option.value }"
        :disabled="!props.enabled || !props.playback || busy"
        @click="saveFeedback(option.value)"
      >
        {{ option.label }}
      </button>
    </div>

    <button
      v-if="currentPreference && !correctionOpen && !collapsed"
      type="button"
      class="motion-feedback__correction-toggle"
      @click="correctionOpen = true"
    >
      Add correction
    </button>

    <div
      v-if="correctionOpen && props.playback && !collapsed"
      class="motion-feedback__correction"
    >
      <label>
        <span>Correct intent</span>
        <select v-model="correctedIntent">
          <option value="">Keep current</option>
          <option v-for="intent in motionIntentNames" :key="intent" :value="intent">
            {{ intent }}
          </option>
        </select>
      </label>
      <label>
        <span>Correct emotion</span>
        <select v-model="correctedEmotion">
          <option value="">Keep current</option>
          <option v-for="emotion in motionEmotions" :key="emotion" :value="emotion">
            {{ emotion }}
          </option>
        </select>
      </label>
      <label class="motion-feedback__intensity">
        <span>Intensity {{ correctedIntensity.toFixed(2) }}</span>
        <input v-model.number="correctedIntensity" type="range" min="0" max="1" step="0.05" />
      </label>
      <div class="motion-feedback__correction-actions">
        <button type="button" :disabled="busy" @click="saveCorrection">
          Save correction
        </button>
        <button type="button" @click="correctionOpen = false">Cancel</button>
      </div>
    </div>

    <p v-if="!props.enabled && !collapsed" class="motion-feedback__notice">
      Motion data collection is disabled in Settings.
    </p>
    <p
      v-else-if="errorMessage && !collapsed"
      class="motion-feedback__error"
      role="alert"
    >
      {{ errorMessage }}
    </p>
    <p
      v-else-if="status && !collapsed"
      class="motion-feedback__status"
      aria-live="polite"
    >
      {{ status }}
    </p>
  </section>
</template>
