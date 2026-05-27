<script setup lang="ts">
import { useToastStore } from "@/stores/toast";

const toast = useToastStore();

function variantClass(v: string) {
  switch (v) {
    case "success":
      return "toast-success";
    case "error":
      return "toast-error";
    case "warning":
      return "toast-warning";
    default:
      return "toast-default";
  }
}
</script>

<template>
  <div class="toast-container" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="t in toast.toasts"
        :key="t.id"
        class="toast-item"
        :class="variantClass(t.variant)"
      >
        <span class="toast-message">{{ t.message }}</span>
        <button class="toast-close" @click="toast.remove(t.id)">&times;</button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-container {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  z-index: 100;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  pointer-events: none;
}

.toast-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 1rem;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 4px 16px oklch(0 0 0 / 0.15);
  font-size: 0.8125rem;
  min-width: 280px;
  max-width: 420px;
  pointer-events: auto;
  border-left: 3px solid var(--color-border);
}

.toast-default {
  border-left-color: var(--color-muted-foreground);
}

.toast-success {
  border-left-color: var(--color-success);
}

.toast-error {
  border-left-color: var(--color-destructive);
}

.toast-warning {
  border-left-color: var(--color-warning);
}

.toast-message {
  flex: 1;
  line-height: 1.4;
}

.toast-close {
  flex-shrink: 0;
  background: none;
  border: none;
  font-size: 1.125rem;
  line-height: 1;
  cursor: pointer;
  color: var(--color-muted-foreground);
  padding: 0;
}

.toast-close:hover {
  color: var(--color-foreground);
}

.toast-enter-active {
  transition: all 0.2s ease-out;
}

.toast-leave-active {
  transition: all 0.15s ease-in;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(1rem);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(1rem);
}
</style>
