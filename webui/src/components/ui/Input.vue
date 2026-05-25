<script setup lang="ts">
import { cn } from "@/lib/utils";
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    modelValue?: string;
    placeholder?: string;
    type?: string;
    disabled?: boolean;
  }>(),
  {
    modelValue: "",
    placeholder: "",
    type: "text",
    disabled: false,
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const classes = computed(() => cn("input", props.disabled && "input-disabled"));
</script>

<template>
  <input
    :class="classes"
    :type="type"
    :placeholder="placeholder"
    :disabled="disabled"
    :value="modelValue"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>

<style scoped>
.input {
  width: 100%;
  height: 32px;
  padding: 0 0.5rem;
  font-size: 0.8125rem;
  background: var(--color-background);
  border: 1px solid var(--color-input);
  border-radius: var(--radius-md);
  color: var(--color-foreground);
  outline: none;
  transition: border-color 0.15s;
}
.input:focus {
  border-color: var(--color-ring);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-ring) 20%, transparent);
}
.input-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
