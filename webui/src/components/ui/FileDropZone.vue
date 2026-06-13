<script setup lang="ts">
import { computed, ref } from "vue";
import { FileText, UploadCloud, X } from "lucide-vue-next";
import { formatBytes } from "@/lib/utils";

const props = withDefaults(
  defineProps<{
    modelValue?: File[];
    accept?: string;
    disabled?: boolean;
    maxFiles?: number;
    maxSizeBytes?: number;
  }>(),
  {
    modelValue: () => [],
    accept: "",
    disabled: false,
    maxFiles: 0,
    maxSizeBytes: 0,
  },
);

const emit = defineEmits<{
  "update:modelValue": [files: File[]];
}>();

const inputRef = ref<HTMLInputElement | null>(null);
const dragDepth = ref(0);
const error = ref("");
const isDragging = computed(() => dragDepth.value > 0);

function openPicker() {
  if (!props.disabled) inputRef.value?.click();
}

function fileKey(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function acceptsFile(file: File) {
  if (!props.accept.trim()) return true;
  const filename = file.name.toLowerCase();
  return props.accept
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean)
    .some((rule) => {
      if (rule.startsWith(".")) return filename.endsWith(rule);
      if (rule.endsWith("/*")) return file.type.startsWith(rule.slice(0, -1));
      return file.type === rule;
    });
}

function addFiles(incoming: File[]) {
  error.value = "";
  const existing = new Map(props.modelValue.map((file) => [fileKey(file), file]));
  const rejected: string[] = [];

  for (const file of incoming) {
    if (!acceptsFile(file)) {
      rejected.push(`${file.name}: unsupported format`);
      continue;
    }
    if (props.maxSizeBytes > 0 && file.size > props.maxSizeBytes) {
      rejected.push(
        `${file.name}: exceeds ${formatBytes(props.maxSizeBytes)}`,
      );
      continue;
    }
    if (!existing.has(fileKey(file))) {
      existing.set(fileKey(file), file);
    }
  }

  let next = [...existing.values()];
  if (props.maxFiles > 0 && next.length > props.maxFiles) {
    rejected.push(`Only the first ${props.maxFiles} files were selected`);
    next = next.slice(0, props.maxFiles);
  }

  emit("update:modelValue", next);
  error.value = rejected.join(". ");
}

function onInput(event: Event) {
  const input = event.target as HTMLInputElement;
  addFiles(Array.from(input.files ?? []));
  input.value = "";
}

function onDragEnter() {
  if (!props.disabled) dragDepth.value += 1;
}

function onDragLeave() {
  dragDepth.value = Math.max(0, dragDepth.value - 1);
}

function onDrop(event: DragEvent) {
  dragDepth.value = 0;
  if (props.disabled) return;
  addFiles(Array.from(event.dataTransfer?.files ?? []));
}

function removeFile(index: number) {
  emit(
    "update:modelValue",
    props.modelValue.filter((_, fileIndex) => fileIndex !== index),
  );
  error.value = "";
}
</script>

<template>
  <div class="file-drop">
    <input
      ref="inputRef"
      class="file-input"
      type="file"
      :accept="accept"
      :disabled="disabled"
      multiple
      @change="onInput"
    />
    <button
      type="button"
      class="drop-zone"
      :class="{ dragging: isDragging, disabled }"
      :disabled="disabled"
      @click="openPicker"
      @dragenter.prevent="onDragEnter"
      @dragover.prevent
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <UploadCloud :size="24" />
      <span><strong>Drop files here</strong> or click to browse</span>
      <small>
        Select up to {{ maxFiles || "multiple" }} files
        <template v-if="maxSizeBytes">
          , {{ formatBytes(maxSizeBytes) }} each
        </template>
      </small>
    </button>

    <p v-if="error" class="drop-error">{{ error }}</p>

    <div v-if="modelValue.length" class="file-list">
      <div
        v-for="(file, index) in modelValue"
        :key="fileKey(file)"
        class="file-row"
      >
        <FileText :size="16" class="file-icon" />
        <span class="file-name" :title="file.name">{{ file.name }}</span>
        <span class="file-size">{{ formatBytes(file.size) }}</span>
        <button
          type="button"
          class="remove-file"
          :aria-label="`Remove ${file.name}`"
          :disabled="disabled"
          @click="removeFile(index)"
        >
          <X :size="14" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.file-drop {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.file-input {
  display: none;
}

.drop-zone {
  width: 100%;
  min-height: 7rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.375rem;
  padding: 1rem;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--color-accent) 35%, transparent);
  color: var(--color-muted-foreground);
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s,
    color 0.15s;
}

.drop-zone:hover,
.drop-zone.dragging {
  border-color: var(--color-primary);
  background: color-mix(in srgb, var(--color-primary) 8%, transparent);
  color: var(--color-foreground);
}

.drop-zone.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.drop-zone span {
  font-size: 0.8125rem;
}

.drop-zone small {
  font-size: 0.75rem;
}

.drop-error {
  margin: 0;
  color: var(--color-destructive);
  font-size: 0.75rem;
  line-height: 1.4;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  max-height: 10rem;
  overflow-y: auto;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2rem;
  padding: 0.25rem 0.375rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-background);
}

.file-icon {
  flex-shrink: 0;
  color: var(--color-primary);
}

.file-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.75rem;
}

.file-size {
  flex-shrink: 0;
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
}

.remove-file {
  flex-shrink: 0;
  display: inline-flex;
  padding: 0.2rem;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted-foreground);
  cursor: pointer;
}

.remove-file:hover {
  background: var(--color-accent);
  color: var(--color-foreground);
}
</style>
