<script setup lang="ts">
import { cn } from "@/lib/utils";

const props = defineProps<{
  tabs: { id: string; label: string }[];
  modelValue: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [id: string];
}>();

const tabClasses = (id: string) =>
  cn("tab-item", props.modelValue === id && "tab-active");
</script>

<template>
  <div class="tabs" role="tablist">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      :class="tabClasses(tab.id)"
      type="button"
      role="tab"
      :aria-selected="modelValue === tab.id"
      @click="emit('update:modelValue', tab.id)"
    >
      {{ tab.label }}
    </button>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 0.375rem;
}

.tab-item {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 64px;
  height: 32px;
  padding: 0 0.75rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-muted-foreground);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  line-height: 1;
  white-space: nowrap;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}

.tab-item:hover {
  background: var(--color-accent);
  color: var(--color-foreground);
}

.tab-active {
  background: var(--color-card);
  border-color: var(--color-border);
  color: var(--color-foreground);
  box-shadow: inset 0 -2px 0 var(--color-primary);
}
</style>
