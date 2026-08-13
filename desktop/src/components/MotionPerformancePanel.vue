<script setup lang="ts">
import {
  createDefaultModelPerformanceProfile,
  type ModelPerformanceProfile,
} from "@/domain/modelPerformanceProfile";
import { normalizedPoseChannels, type NormalizedPoseChannel } from "@/domain/normalizedPose";

const props = defineProps<{
  profile: ModelPerformanceProfile;
}>();

const emit = defineEmits<{
  update: [profile: ModelPerformanceProfile];
}>();

function numericValue(event: Event): number {
  return Number((event.target as HTMLInputElement).value);
}

function updateRoot(
  field: "intensityScale" | "preferredIdleEnergy",
  event: Event,
): void {
  emit("update", { ...props.profile, [field]: numericValue(event) });
}

function updateParameterIds(channel: NormalizedPoseChannel, event: Event): void {
  const parameterIds = (event.target as HTMLInputElement).value
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
  emit("update", {
    ...props.profile,
    poseParameterMap: {
      ...props.profile.poseParameterMap,
      [channel]: parameterIds,
    },
  });
}

function updateLimit(
  field: "maxVelocity" | "maxAcceleration",
  channel: NormalizedPoseChannel,
  event: Event,
): void {
  emit("update", {
    ...props.profile,
    [field]: {
      ...props.profile[field],
      [channel]: numericValue(event),
    },
  });
}
</script>

<template>
  <section class="panel motion-performance" aria-label="Motion performance calibration">
    <header class="panel__header">
      <h2>Performance Profile</h2>
      <button
        type="button"
        @click="emit('update', createDefaultModelPerformanceProfile(props.profile.modelId))"
      >
        Reset
      </button>
    </header>

    <div class="motion-performance__summary">
      <label>
        <span>Intensity scale</span>
        <input
          type="number"
          min="0"
          max="2"
          step="0.05"
          :value="props.profile.intensityScale"
          @change="updateRoot('intensityScale', $event)"
        />
      </label>
      <label>
        <span>Idle energy</span>
        <input
          type="number"
          min="0"
          max="1"
          step="0.05"
          :value="props.profile.preferredIdleEnergy"
          @change="updateRoot('preferredIdleEnergy', $event)"
        />
      </label>
    </div>

    <details>
      <summary>Canonical channel calibration</summary>
      <div class="motion-performance__channels">
        <label v-for="channel in normalizedPoseChannels" :key="channel">
          <strong>{{ channel }}</strong>
          <input
            type="text"
            :value="props.profile.poseParameterMap[channel].join(', ')"
            placeholder="Live2D parameter IDs"
            @change="updateParameterIds(channel, $event)"
          />
          <input
            type="number"
            min="0.01"
            max="100"
            step="0.1"
            :value="props.profile.maxVelocity[channel]"
            title="Maximum normalized velocity per second"
            @change="updateLimit('maxVelocity', channel, $event)"
          />
          <input
            type="number"
            min="0.01"
            max="1000"
            step="0.1"
            :value="props.profile.maxAcceleration[channel]"
            title="Maximum normalized acceleration per second squared"
            @change="updateLimit('maxAcceleration', channel, $event)"
          />
        </label>
      </div>
    </details>
  </section>
</template>
