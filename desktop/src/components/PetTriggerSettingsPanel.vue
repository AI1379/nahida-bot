<script setup lang="ts">
import type { PetTriggerSettings } from "@/domain/config";

const props = defineProps<{
  settings: PetTriggerSettings;
}>();

const emit = defineEmits<{
  update: [settings: PetTriggerSettings];
}>();

function update(patch: Partial<PetTriggerSettings>) {
  emit("update", {
    ...props.settings,
    ...patch,
  });
}

function changeDistance(
  key: "wakeDistancePx" | "hideDistancePx",
  event: Event,
) {
  update({ [key]: Number((event.target as HTMLInputElement).value) });
}

function changeDuration(
  key: "autoRetreatMs" | "chatIdleTimeoutMs",
  event: Event,
) {
  update({
    [key]: Number((event.target as HTMLInputElement).value) * 1000,
  });
}
</script>

<template>
  <section class="panel pet-trigger-settings" aria-label="Pet trigger settings">
    <header class="panel__header">
      <h2>Pet Triggers</h2>
      <span>proximity &amp; idle timing</span>
    </header>

    <div class="pet-trigger-settings__body">
      <label>
        <span>Wake distance: {{ settings.wakeDistancePx }} px</span>
        <input
          type="range"
          min="8"
          max="240"
          step="2"
          :value="settings.wakeDistancePx"
          @input="changeDistance('wakeDistancePx', $event)"
        />
      </label>

      <label>
        <span>Hide distance: {{ settings.hideDistancePx }} px</span>
        <input
          type="range"
          min="16"
          max="480"
          step="2"
          :value="settings.hideDistancePx"
          @input="changeDistance('hideDistancePx', $event)"
        />
      </label>

      <label>
        <span>
          Auto retreat: {{ Math.round(settings.autoRetreatMs / 1000) }} s
        </span>
        <input
          type="range"
          min="2"
          max="120"
          step="1"
          :value="Math.round(settings.autoRetreatMs / 1000)"
          @input="changeDuration('autoRetreatMs', $event)"
        />
      </label>

      <label>
        <span>
          Chat idle timeout:
          {{ Math.round(settings.chatIdleTimeoutMs / 1000) }} s
        </span>
        <input
          type="range"
          min="10"
          max="300"
          step="5"
          :value="Math.round(settings.chatIdleTimeoutMs / 1000)"
          @input="changeDuration('chatIdleTimeoutMs', $event)"
        />
      </label>

      <p class="pet-trigger-settings__note">
        Wake distance is how close the cursor must get to the hidden pet
        before it peeks out; hide distance is how far away it must move
        again. The hide distance always stays beyond the wake distance.
      </p>
    </div>
  </section>
</template>
