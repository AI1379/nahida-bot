<script setup lang="ts">
/**
 * PluginConfigForm.vue
 *
 * Dynamically renders a configuration form from schema entries
 * (sourced from /api/config/schema), and saves changes via the
 * existing PATCH /api/config/current endpoint.
 *
 * Works for ALL plugins — the backend infers types for plugins
 * without explicit config_schema.
 */
import { computed, ref, watch } from "vue";
import { useConfigPatchSave } from "@/api/queries";
import type { ConfigPatchChange, ConfigSaveResponse } from "@/api/schemas";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import Textarea from "@/components/ui/Textarea.vue";
import {
  schemaEntriesToFields,
  getAtPath,
  setAtPath,
  cloneConfig,
  buildPluginChanges,
} from "./jsonSchemaForm";
import type { SchemaField, SchemaEntryData } from "./jsonSchemaForm";

type ConfigMap = Record<string, unknown>;

// ---------------------------------------------------------------------------
// Props & Emits
// ---------------------------------------------------------------------------

const props = defineProps<{
  pluginId: string;
  /** Flat schema entries from /api/config/schema */
  schemaEntries: SchemaEntryData[];
  /** Current config values from /api/config/document → data[pluginId] */
  currentValues: ConfigMap;
  /** Paths that are redacted (show "***") */
  redactedPaths: Set<string>;
  /** config.yaml checksum for optimistic concurrency */
  checksum: string;
}>();

const emit = defineEmits<{
  saved: [];
}>();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const draft = ref<ConfigMap>({});
const original = ref<ConfigMap>({});
const baseChecksum = ref("");
const showSaveDialog = ref(false);
const pendingChanges = ref<ConfigPatchChange[]>([]);

const saveMutation = useConfigPatchSave();

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const fields = computed<SchemaField[]>(() =>
  schemaEntriesToFields(props.schemaEntries, props.pluginId),
);

const hasChanges = computed(
  () => JSON.stringify(draft.value) !== JSON.stringify(original.value),
);

const changeCount = computed(() =>
  buildPluginChanges(
    props.pluginId,
    original.value,
    draft.value,
    props.redactedPaths,
  ).length,
);

// ---------------------------------------------------------------------------
// Draft sync
// ---------------------------------------------------------------------------

watch(
  () => props.currentValues,
  (vals) => {
    if (!vals || !Object.keys(vals).length) {
      // No current values — populate from schema defaults
      const defaults: ConfigMap = {};
      for (const field of fields.value) {
        if (field.default !== undefined) {
          setAtPath(defaults, field.path, field.default);
        }
      }
      draft.value = defaults;
      original.value = cloneConfig(defaults);
    } else {
      draft.value = cloneConfig(vals);
      original.value = cloneConfig(vals);
    }
  },
  { immediate: true },
);

watch(
  () => props.checksum,
  (cs) => {
    baseChecksum.value = cs;
  },
  { immediate: true },
);

// ---------------------------------------------------------------------------
// Field accessors
// ---------------------------------------------------------------------------

function getValue(path: string): unknown {
  return getAtPath(draft.value, path);
}

function fieldText(path: string): string {
  const value = getValue(path);
  if (value === null || value === undefined) return "";
  return String(value);
}

function fieldBool(path: string): boolean {
  return Boolean(getValue(path));
}

function fieldListText(path: string): string {
  const value = getValue(path);
  return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "";
}

function secretInputValue(path: string): string {
  if (props.redactedPaths.has(path) && fieldText(path) === "***") return "";
  return fieldText(path);
}

function secretPlaceholder(path: string): string {
  return props.redactedPaths.has(path) ? "unchanged" : "";
}

function isWideField(field: SchemaField): boolean {
  return (
    field.kind === "array-string" ||
    field.kind === "array-number" ||
    field.kind === "secret"
  );
}

// ---------------------------------------------------------------------------
// Field updaters
// ---------------------------------------------------------------------------

function updateText(path: string, value: string): void {
  setAtPath(draft.value, path, value);
}

function updateNumber(path: string, raw: string): void {
  if (raw.trim() === "") {
    setAtPath(draft.value, path, null);
    return;
  }
  const value = Number(raw);
  if (Number.isFinite(value)) setAtPath(draft.value, path, value);
}

function updateBool(path: string, value: boolean): void {
  setAtPath(draft.value, path, value);
}

function updateList(path: string, raw: string): void {
  const values = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  setAtPath(draft.value, path, values);
}

function clearSecret(path: string): void {
  setAtPath(draft.value, path, "");
}

function renderFieldKey(field: SchemaField): string {
  return `${props.pluginId}_${field.path.replaceAll(".", "_")}`;
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

function requestSave(): void {
  pendingChanges.value = buildPluginChanges(
    props.pluginId,
    original.value,
    draft.value,
    props.redactedPaths,
  );
  if (!pendingChanges.value.length) return;
  showSaveDialog.value = true;
}

function confirmSave(): void {
  showSaveDialog.value = false;
  saveMutation.mutate(
    {
      expected_checksum: baseChecksum.value,
      changes: pendingChanges.value,
    },
    {
      onSuccess: (data: ConfigSaveResponse) => {
        original.value = cloneConfig(draft.value);
        baseChecksum.value = data.checksum;
        emit("saved");
      },
    },
  );
}

function discardChanges(): void {
  draft.value = cloneConfig(original.value);
}
</script>

<template>
  <div class="plugin-config-form">
    <div class="form-header">
      <h4>Edit Configuration</h4>
      <p>Changes require an application restart to take effect.</p>
    </div>

    <div class="field-grid">
      <div
        v-for="field in fields"
        :key="field.path"
        class="field-row"
        :class="{ wide: isWideField(field) }"
      >
        <label :for="renderFieldKey(field)">
          <span>{{ field.label }}</span>
          <code>{{ field.path }}</code>
        </label>

        <select
          v-if="field.kind === 'select'"
          :id="renderFieldKey(field)"
          :value="fieldText(field.path)"
          @change="updateText(field.path, ($event.target as HTMLSelectElement).value)"
        >
          <option v-for="opt in field.options" :key="opt" :value="opt">
            {{ opt || "auto" }}
          </option>
        </select>

        <input
          v-else-if="field.kind === 'boolean'"
          :id="renderFieldKey(field)"
          type="checkbox"
          :checked="fieldBool(field.path)"
          @change="updateBool(field.path, ($event.target as HTMLInputElement).checked)"
        />

        <input
          v-else-if="field.kind === 'number' || field.kind === 'integer'"
          :id="renderFieldKey(field)"
          type="number"
          :step="field.kind === 'integer' ? 1 : 'any'"
          :value="fieldText(field.path)"
          :min="field.minimum"
          :max="field.maximum"
          @input="updateNumber(field.path, ($event.target as HTMLInputElement).value)"
        />

        <div v-else-if="field.kind === 'secret'" class="secret-control">
          <input
            :id="renderFieldKey(field)"
            type="password"
            :placeholder="secretPlaceholder(field.path)"
            :value="secretInputValue(field.path)"
            @input="updateText(field.path, ($event.target as HTMLInputElement).value)"
          />
          <Button variant="outline" size="sm" @click="clearSecret(field.path)">
            Clear
          </Button>
        </div>

        <Textarea
          v-else-if="field.kind === 'array-string' || field.kind === 'array-number'"
          :id="renderFieldKey(field)"
          :model-value="fieldListText(field.path)"
          :rows="3"
          @update:model-value="updateList(field.path, $event)"
        />

        <!-- Default: text -->
        <input
          v-else
          :id="renderFieldKey(field)"
          type="text"
          :value="fieldText(field.path)"
          :placeholder="field.description ?? ''"
          @input="updateText(field.path, ($event.target as HTMLInputElement).value)"
        />
      </div>
    </div>

    <div v-if="!fields.length" class="muted">
      No configurable fields defined.
    </div>

    <div v-if="fields.length" class="form-actions">
      <Button
        variant="ghost"
        size="sm"
        :disabled="!hasChanges"
        @click="discardChanges"
      >
        Discard
      </Button>
      <Button
        variant="default"
        size="sm"
        :disabled="!hasChanges"
        @click="requestSave"
      >
        Save
        <Badge v-if="changeCount" variant="secondary"> {{ changeCount }} </Badge>
      </Button>
    </div>

    <ConfirmDialog
      v-model:open="showSaveDialog"
      title="Save Plugin Configuration"
      :description="`Apply ${pendingChanges.length} change(s) to ${pluginId} configuration. A backup will be created and a restart is required.`"
      confirm-label="Save"
      :loading="saveMutation.isPending.value"
      @confirm="confirmSave"
    />
  </div>
</template>

<style scoped>
.plugin-config-form {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.form-header {
  border-top: 1px solid var(--color-border);
  padding-top: 0.75rem;
}

.form-header h4 {
  margin: 0;
  font-size: 0.8125rem;
  font-weight: 600;
}

.form-header p {
  margin: 0.25rem 0 0;
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

.muted {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem 1rem;
}

.field-row {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 0;
}

.field-row.wide {
  grid-column: 1 / -1;
}

.field-row label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  color: var(--color-foreground);
  font-size: 0.8125rem;
  font-weight: 500;
}

.field-row label code {
  color: var(--color-muted-foreground);
  font-weight: 400;
  max-width: 50%;
  overflow-wrap: anywhere;
  text-align: right;
  background: var(--color-muted);
  border-radius: var(--radius-sm);
  padding: 0.0625rem 0.3125rem;
  font-family: var(--font-mono);
  font-size: 0.72rem;
}

input[type="text"],
input[type="password"],
input[type="number"],
select {
  width: 100%;
  height: 32px;
  border: 1px solid var(--color-input);
  border-radius: var(--radius-md);
  background: var(--color-background);
  color: var(--color-foreground);
  font-size: 0.8125rem;
  outline: none;
  padding: 0 0.5rem;
}

input[type="checkbox"] {
  width: 18px;
  height: 18px;
}

input:focus,
select:focus {
  border-color: var(--color-ring);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-ring) 20%, transparent);
}

.secret-control {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.secret-control input {
  min-width: 0;
}

.form-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
  padding-top: 0.25rem;
}

.form-actions .btn {
  gap: 0.25rem;
}
</style>
