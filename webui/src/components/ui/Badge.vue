<script setup lang="ts">
import { cn } from "@/lib/utils";
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    variant?: "default" | "success" | "warning" | "destructive" | "secondary" | "outline";
  }>(),
  { variant: "default" },
);

const classes = computed(() => cn("badge", `badge-${props.variant}`));
</script>

<template>
  <span :class="classes"><slot /></span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.125rem 0.5rem;
  font-size: 0.6875rem;
  font-weight: 500;
  border-radius: 9999px;
  line-height: 1.4;
  white-space: nowrap;
}
.badge-default {
  background: var(--color-primary);
  color: var(--color-primary-foreground);
}
.badge-success {
  background: color-mix(in srgb, var(--color-success) 15%, transparent);
  color: var(--color-success);
}
.badge-warning {
  background: color-mix(in srgb, var(--color-warning) 25%, transparent);
  color: var(--color-warning);
}
.badge-destructive {
  background: color-mix(in srgb, var(--color-destructive) 25%, transparent);
  color: var(--color-destructive);
}
.badge-secondary {
  background: var(--color-secondary);
  color: var(--color-secondary-foreground);
}
.badge-outline {
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-foreground);
}
</style>
