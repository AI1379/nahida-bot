<script setup lang="ts">
import {
  DialogRoot,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "reka-ui";
import Button from "./Button.vue";
import Spinner from "./Spinner.vue";

withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    description?: string;
    variant?: "default" | "destructive";
    confirmLabel?: string;
    cancelLabel?: string;
    loading?: boolean;
    disabled?: boolean;
    size?: "default" | "wide";
    showCancel?: boolean;
  }>(),
  {
    description: "",
    variant: "default",
    confirmLabel: "Confirm",
    cancelLabel: "Cancel",
    loading: false,
    disabled: false,
    size: "default",
    showCancel: true,
  },
);

const emit = defineEmits<{
  confirm: [];
  "update:open": [value: boolean];
}>();
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="dialog-overlay" />
      <DialogContent
        class="dialog-content"
        :class="{ 'dialog-content--wide': size === 'wide' }"
      >
        <div class="dialog-header">
          <DialogTitle class="dialog-title">{{ title }}</DialogTitle>
          <DialogClose as-child>
            <button class="dialog-close" aria-label="Close">&times;</button>
          </DialogClose>
        </div>
        <div class="dialog-body">
          <DialogDescription v-if="description" class="dialog-desc">
            {{ description }}
          </DialogDescription>
          <slot />
        </div>
        <div class="dialog-footer">
          <DialogClose v-if="showCancel" as-child>
            <Button variant="outline" size="sm" :disabled="loading">
              {{ cancelLabel }}
            </Button>
          </DialogClose>
          <Button
            :variant="variant === 'destructive' ? 'destructive' : 'default'"
            size="sm"
            :disabled="loading || disabled"
            @click="emit('confirm')"
          >
            <Spinner v-if="loading" size="sm" />
            {{ confirmLabel }}
          </Button>
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: oklch(0 0 0 / 0.4);
  animation: overlay-in 0.15s ease-out;
}

@keyframes overlay-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.dialog-content {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 51;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  width: min(440px, 90vw);
  max-height: calc(100dvh - 2rem);
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px oklch(0 0 0 / 0.2);
  animation: content-in 0.15s ease-out;
}

.dialog-content--wide {
  width: min(48rem, calc(100vw - 2rem));
}

@keyframes content-in {
  from { opacity: 0; transform: translate(-50%, -48%); }
  to { opacity: 1; transform: translate(-50%, -50%); }
}

.dialog-content:focus {
  outline: none;
}

.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.dialog-title {
  font-size: 0.875rem;
  font-weight: 600;
}

.dialog-close {
  background: none;
  border: none;
  font-size: 1.25rem;
  cursor: pointer;
  color: var(--color-muted-foreground);
  padding: 0 0.25rem;
  line-height: 1;
}

.dialog-close:hover {
  color: var(--color-foreground);
}

.dialog-body {
  padding: 1rem;
  min-height: 0;
  overflow-y: auto;
}

.dialog-desc {
  font-size: 0.8125rem;
  color: var(--color-muted-foreground);
  line-height: 1.5;
  margin: 0;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--color-border);
}
</style>
