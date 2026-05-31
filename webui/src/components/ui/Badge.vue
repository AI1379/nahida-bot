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
  font-weight: 600;
  border-radius: 9999px;
  border: 1px solid transparent;
  line-height: 1.4;
  white-space: nowrap;
}
.badge-default {
  background: color-mix(in srgb, var(--color-primary) 12%, transparent);
  border-color: color-mix(in srgb, var(--color-primary) 22%, transparent);
  color: var(--color-primary);
}
.badge-success {
  background: color-mix(in srgb, var(--color-success) 12%, transparent);
  border-color: color-mix(in srgb, var(--color-success) 24%, transparent);
  color: var(--color-success);
  box-shadow: 0 0 10px color-mix(in srgb, var(--color-success) 7%, transparent);
}
.badge-warning {
  background: color-mix(in srgb, var(--color-warning) 16%, transparent);
  border-color: color-mix(in srgb, var(--color-warning) 30%, transparent);
  color: var(--color-warning);
}
.badge-destructive {
  background: color-mix(in srgb, var(--color-destructive) 16%, transparent);
  border-color: color-mix(in srgb, var(--color-destructive) 32%, transparent);
  color: var(--color-destructive);
}
.badge-secondary {
  background: var(--color-secondary);
  border-color: var(--color-border);
  color: var(--color-secondary-foreground);
}
.badge-outline {
  background: color-mix(in srgb, var(--color-card) 74%, transparent);
  border: 1px solid var(--color-border);
  color: var(--color-muted-foreground);
}
</style>
