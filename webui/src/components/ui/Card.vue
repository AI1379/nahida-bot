<script setup lang="ts">
import { cn } from "@/lib/utils";
import { computed } from "vue";

const props = defineProps<{
  class?: string;
}>();

const classes = computed(() => cn("card", props.class));
</script>

<template>
  <div :class="classes">
    <slot />
  </div>
</template>

<style scoped>
.card {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  padding: 1rem;
}
</style>
