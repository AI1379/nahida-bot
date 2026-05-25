<script setup lang="ts">
import { cn } from "@/lib/utils";
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    variant?: "default" | "destructive" | "success" | "warning";
  }>(),
  { variant: "default" },
);

const classes = computed(() => cn("alert", `alert-${props.variant}`));
</script>

<template>
  <div :class="classes" role="alert">
    <slot />
  </div>
</template>

<style scoped>
.alert {
  padding: 0.75rem 1rem;
  border-radius: var(--radius-md);
  font-size: 0.8125rem;
  border: 1px solid;
}
.alert-default {
  background: var(--color-muted);
  border-color: var(--color-border);
  color: var(--color-foreground);
}
.alert-destructive {
  background: color-mix(in srgb, var(--color-destructive) 10%, transparent);
  border-color: color-mix(in srgb, var(--color-destructive) 30%, transparent);
  color: var(--color-destructive);
}
.alert-success {
  background: color-mix(in srgb, var(--color-success) 10%, transparent);
  border-color: color-mix(in srgb, var(--color-success) 30%, transparent);
  color: var(--color-success);
}
.alert-warning {
  background: color-mix(in srgb, var(--color-warning) 10%, transparent);
  border-color: color-mix(in srgb, var(--color-warning) 30%, transparent);
  color: var(--color-warning);
}
</style>
