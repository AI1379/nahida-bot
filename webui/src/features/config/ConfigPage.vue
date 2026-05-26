<script setup lang="ts">
import { ref, computed, watchEffect } from "vue";
import { useConfigCurrent, useConfigSchema } from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Tabs from "@/components/ui/Tabs.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
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
        <pre class="yaml-pre">{{ configData.content }}</pre>
      </div>
    </template>
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
}

.muted {
  color: var(--color-muted-foreground);
}
</style>
