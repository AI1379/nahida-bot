<script setup lang="ts">
import { cn } from "@/lib/utils";
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    variant?: "default" | "destructive" | "outline" | "ghost" | "secondary";
    size?: "default" | "sm" | "lg" | "icon";
    disabled?: boolean;
    type?: "button" | "submit" | "reset";
  }>(),
  {
    variant: "default",
    size: "default",
    disabled: false,
    type: "button",
  },
);

const classes = computed(() =>
  cn(
    "btn",
    `btn-${props.variant}`,
    `btn-${props.size}`,
    props.disabled && "btn-disabled",
  ),
);
</script>

<template>
  <button :class="classes" :disabled="disabled" :type="type">
    <slot />
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.375rem;
  border-radius: var(--radius-md);
  font-size: 0.8125rem;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  border: none;
  transition: background 0.15s, color 0.15s, opacity 0.15s;
  white-space: nowrap;
}
.btn:focus-visible {
  outline: 2px solid var(--color-ring);
  outline-offset: 2px;
}

.btn-default {
  padding: 0.5rem 0.875rem;
  background: var(--color-primary);
  color: var(--color-primary-foreground);
}
.btn-default:hover {
  opacity: 0.9;
}

.btn-destructive {
  padding: 0.5rem 0.875rem;
  background: var(--color-destructive);
  color: white;
}
.btn-destructive:hover {
  opacity: 0.9;
}

.btn-outline {
  padding: 0.5rem 0.875rem;
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-foreground);
}
.btn-outline:hover {
  background: var(--color-accent);
}

.btn-ghost {
  padding: 0.5rem 0.875rem;
  background: transparent;
  color: var(--color-foreground);
}
.btn-ghost:hover {
  background: var(--color-accent);
}

.btn-secondary {
  padding: 0.5rem 0.875rem;
  background: var(--color-secondary);
  color: var(--color-secondary-foreground);
}
.btn-secondary:hover {
  opacity: 0.9;
}

.btn-sm {
  padding: 0.3125rem 0.625rem;
  font-size: 0.75rem;
}

.btn-lg {
  padding: 0.625rem 1.125rem;
}

.btn-icon {
  padding: 0.5rem;
  width: 32px;
  height: 32px;
}

.btn-disabled {
  pointer-events: none;
  opacity: 0.5;
}
</style>
