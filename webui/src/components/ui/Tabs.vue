<script setup lang="ts">
import { cn } from "@/lib/utils";
import { computed } from "vue";

const props = defineProps<{
  tabs: { id: string; label: string }[];
  modelValue: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [id: string];
}>();

const activeClasses = (id: string) =>
  computed(() =>
    cn(
      "tab-item",
      props.modelValue === id && "tab-active",
    ),
  );
</script>

<template>
  <div class="tabs" role="tablist">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      :class="activeClasses(tab.id)"
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
  gap: 0;
  border-bottom: 1px solid var(--color-border);
}

.tab-item {
  padding: 0.5rem 1rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-muted-foreground);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}

.tab-item:hover {
  color: var(--color-foreground);
}

.tab-active {
  color: var(--color-foreground);
  border-bottom-color: var(--color-primary);
}
</style>
