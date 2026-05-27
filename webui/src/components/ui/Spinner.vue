<script setup lang="ts">
import { computed } from "vue";
import { cn } from "@/lib/utils";

const props = withDefaults(
  defineProps<{ size?: "sm" | "default" | "lg" }>(),
  { size: "default" },
);

const classes = computed(() =>
  cn("spinner", {
    "spinner-sm": props.size === "sm",
    "spinner-lg": props.size === "lg",
  }),
);
</script>

<template>
  <div :class="classes" role="status" aria-label="Loading">
    <svg viewBox="0 0 24 24" fill="none">
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
        opacity="0.25"
      />
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
        stroke-dasharray="31.4 31.4"
        class="spinner-arc"
      />
    </svg>
  </div>
</template>

<style scoped>
.spinner {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-muted-foreground);
}

.spinner svg {
  width: 20px;
  height: 20px;
}

.spinner-sm svg {
  width: 14px;
  height: 14px;
}

.spinner-lg svg {
  width: 28px;
  height: 28px;
}

.spinner-arc {
  animation: spin 0.75s linear infinite;
  transform-origin: center;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
