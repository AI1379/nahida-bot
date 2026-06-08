<script setup lang="ts">
import type { DisplayPlan } from "@/domain/displayPlan";

defineProps<{
  plan: DisplayPlan | null;
  activeIndex: number;
}>();
</script>

<template>
  <section class="panel plan" aria-label="Display plan">
    <header class="panel__header">
      <h2>DisplayPlan</h2>
      <span>{{ plan ? `${plan.segments.length} segments` : "empty" }}</span>
    </header>

    <div v-if="!plan" class="plan__empty">
      Waiting for mock agent output.
    </div>
    <ol v-else class="plan__segments">
      <li
        v-for="(segment, index) in plan.segments"
        :key="`${segment.text}-${index}`"
        :class="{ 'is-active': index === activeIndex }"
      >
        <div>
          <strong>{{ segment.emotion ?? "neutral" }}</strong>
          <span>{{ segment.motion ?? "speaking" }}</span>
        </div>
        <p>{{ segment.text }}</p>
      </li>
    </ol>
  </section>
</template>
