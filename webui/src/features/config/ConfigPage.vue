<script setup lang="ts">
import { ref, computed, watchEffect } from "vue";
import { useConfigCurrent, useConfigSchema, useConfigSave } from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Tabs from "@/components/ui/Tabs.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Spinner from "@/components/ui/Spinner.vue";
import Textarea from "@/components/ui/Textarea.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import { api } from "@/api/client";
import type { ConfigValidateResponse } from "@/api/schemas";

const activeTab = ref("schema");
const tabs = [
  { id: "schema", label: "Schema" },
  { id: "yaml", label: "YAML" },
];

const { data: configData, isLoading: configLoading, error: configError } = useConfigCurrent();
const { data: schemaData, isLoading: schemaLoading } = useConfigSchema();

const validation = ref<ConfigValidateResponse | null>(null);
const validating = ref(false);

const isEditing = ref(false);
const editContent = ref("");
const baseChecksum = ref("");
const showSaveDialog = ref(false);

const saveMutation = useConfigSave();

watchEffect(async () => {
  if (configData.value && !validation.value) {
    validating.value = true;
    try {
      validation.value = await api.post<ConfigValidateResponse>(
        "/config/validate",
        { content: configData.value.content },
      );
    } catch {
      /* validation endpoint may fail */
    } finally {
      validating.value = false;
    }
  }
});

const validationVariant = computed(() => {
  if (!validation.value) return "default";
  if (validation.value.errors > 0) return "destructive";
  if (validation.value.warnings > 0) return "warning";
  return "success";
});

function startEditing() {
  if (!configData.value) return;
  editContent.value = configData.value.content;
  baseChecksum.value = configData.value.checksum;
  isEditing.value = true;
}

function cancelEditing() {
  isEditing.value = false;
  editContent.value = "";
}

function requestSave() {
  showSaveDialog.value = true;
}

function confirmSave() {
  showSaveDialog.value = false;
  saveMutation.mutate(
    {
      content: editContent.value,
      expected_checksum: baseChecksum.value,
      format: "yaml",
    },
    {
      onSuccess: () => {
        isEditing.value = false;
        // Re-validate after save
        validation.value = null;
      },
    },
  );
}
</script>

<template>
  <div class="config-page">
    <Alert v-if="configError" variant="destructive">
      Failed to load config: {{ configError.message }}
    </Alert>

    <div v-if="configLoading || schemaLoading" class="loading">Loading...</div>

    <template v-if="configData">
      <!-- Meta info -->
      <div class="config-meta">
        <span class="meta-item">
          Path: <code>{{ configData.path }}</code>
        </span>
        <span class="meta-item">
          Modified: {{ configData.mtime }}
        </span>
        <Badge variant="outline">Checksum: {{ configData.checksum.slice(0, 12) }}...</Badge>
      </div>

      <!-- Validation -->
      <Card v-if="validation" class="validation-card">
        <div class="validation-header">
          <span class="validation-title">Validation</span>
          <Badge :variant="validationVariant">
            {{ validation.ok ? "OK" : `${validation.errors} errors, ${validation.warnings} warnings` }}
          </Badge>
        </div>
        <div v-if="validation.issues.length" class="validation-issues">
          <div
            v-for="(issue, i) in validation.issues"
            :key="i"
            class="issue-item"
            :class="issue.severity"
          >
            <Badge :variant="issue.severity === 'error' ? 'destructive' : 'warning'" size="sm">
              {{ issue.severity }}
            </Badge>
            <span>{{ issue.message }}</span>
            <code v-if="issue.path" class="issue-field">{{ issue.path }}</code>
          </div>
        </div>
      </Card>

      <!-- Tabs -->
      <Tabs :tabs="tabs" v-model="activeTab" />

      <!-- Schema view -->
      <div v-if="activeTab === 'schema' && schemaData" class="schema-view">
        <table class="schema-table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Type</th>
              <th>Default</th>
              <th>Constraints</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in schemaData.entries" :key="entry.path">
              <td><code>{{ entry.path }}</code></td>
              <td>{{ entry.type }}</td>
              <td>
                <code v-if="entry.default">{{ entry.default }}</code>
                <span v-else class="muted">-</span>
              </td>
              <td>{{ entry.constraints }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- YAML view -->
      <div v-if="activeTab === 'yaml'" class="yaml-view">
        <div class="yaml-toolbar">
          <template v-if="!isEditing">
            <Button size="sm" @click="startEditing">Edit</Button>
          </template>
          <template v-else>
            <Button size="sm" :disabled="saveMutation.isPending.value" @click="requestSave">
              <Spinner v-if="saveMutation.isPending.value" size="sm" />
              Save
            </Button>
            <Button size="sm" variant="outline" :disabled="saveMutation.isPending.value" @click="cancelEditing">
              Reset
            </Button>
          </template>
        </div>
        <div v-if="!isEditing" class="yaml-pre">{{ configData.content }}</div>
        <Textarea
          v-else
          v-model="editContent"
          :rows="30"
          class="yaml-editor"
        />
      </div>
    </template>

    <ConfirmDialog
      v-model:open="showSaveDialog"
      title="Save Configuration"
      description="This will overwrite config.yaml with your changes. A restart is required for changes to take effect."
      confirm-label="Save"
      :loading="saveMutation.isPending.value"
      @confirm="confirmSave"
    />
  </div>
</template>

<style scoped>
.config-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.config-meta {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.meta-item code {
  background: var(--color-muted);
  padding: 0.125rem 0.375rem;
  border-radius: var(--radius-sm);
  font-size: 0.6875rem;
}

.validation-card {
  padding: 0.75rem 1rem;
}

.validation-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.validation-title {
  font-weight: 600;
  font-size: 0.8125rem;
}

.validation-issues {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.issue-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}

.issue-field {
  background: var(--color-muted);
  padding: 0.0625rem 0.375rem;
  border-radius: var(--radius-sm);
  font-size: 0.6875rem;
}

.schema-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.schema-table th {
  text-align: left;
  padding: 0.5rem;
  border-bottom: 1px solid var(--color-border);
  font-weight: 600;
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.schema-table td {
  padding: 0.5rem;
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
}

.schema-table code {
  background: var(--color-muted);
  padding: 0.0625rem 0.375rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
}

.yaml-view {
  margin-top: 0.5rem;
}

.yaml-toolbar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.yaml-pre {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem;
  font-size: 0.8125rem;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-family: var(--font-mono);
}

.yaml-editor {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
  resize: vertical;
  min-height: 400px;
}

.muted {
  color: var(--color-muted-foreground);
}
</style>
