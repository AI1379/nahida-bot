<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  Archive,
  Play,
  Power,
  PowerOff,
  RefreshCw,
  Search,
} from "lucide-vue-next";
import { usePluginAction, usePluginList } from "@/api/queries";
import type {
  PluginAction,
  PluginState,
  PluginSummary,
} from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "destructive"
  | "secondary"
  | "outline";

const stateFilter = ref<PluginState | "all">("all");
const search = ref("");
const selectedId = ref("");

const {
  data: pluginData,
  isLoading,
  isFetching,
  error,
  refetch,
} = usePluginList();
const actionMutation = usePluginAction();

const states: PluginState[] = [
  "found",
  "loaded",
  "enabled",
  "disabled",
  "error",
  "unloaded",
];

const stateMeta: Record<
  PluginState,
  { label: string; variant: BadgeVariant }
> = {
  found: { label: "Found", variant: "outline" },
  loaded: { label: "Loaded", variant: "default" },
  enabled: { label: "Enabled", variant: "success" },
  disabled: { label: "Disabled", variant: "warning" },
  error: { label: "Error", variant: "destructive" },
  unloaded: { label: "Unloaded", variant: "secondary" },
};

const plugins = computed(() => pluginData.value?.plugins ?? []);

const filteredPlugins = computed(() => {
  const term = search.value.trim().toLowerCase();
  return plugins.value.filter((plugin) => {
    const matchesState =
      stateFilter.value === "all" || plugin.state === stateFilter.value;
    if (!matchesState) return false;
    if (!term) return true;
    return [
      plugin.id,
      plugin.name,
      plugin.description,
      plugin.entrypoint,
      plugin.path,
    ]
      .join(" ")
      .toLowerCase()
      .includes(term);
  });
});

watch(
  filteredPlugins,
  (items) => {
    if (!items.length) {
      selectedId.value = "";
      return;
    }
    if (!items.some((plugin) => plugin.id === selectedId.value)) {
      selectedId.value = items[0].id;
    }
  },
  { immediate: true },
);

const selectedPlugin = computed(() =>
  filteredPlugins.value.find((plugin) => plugin.id === selectedId.value),
);

const stateCounts = computed(() => {
  const counts = Object.fromEntries(states.map((state) => [state, 0])) as Record<
    PluginState,
    number
  >;
  for (const plugin of plugins.value) counts[plugin.state] += 1;
  return counts;
});

function stateVariant(state: PluginState): BadgeVariant {
  return stateMeta[state]?.variant ?? "outline";
}

function stateLabel(state: PluginState): string {
  return stateMeta[state]?.label ?? state;
}

function canAction(plugin: PluginSummary, action: PluginAction): boolean {
  switch (action) {
    case "load":
      return plugin.state === "found";
    case "enable":
      return plugin.state === "loaded" || plugin.state === "disabled";
    case "disable":
      return plugin.state === "enabled";
    case "reload":
      return ["enabled", "disabled", "loaded", "error", "unloaded"].includes(
        plugin.state,
      );
    case "unload":
      return ["loaded", "disabled", "error"].includes(plugin.state);
  }
}

function isPending(plugin: PluginSummary, action: PluginAction): boolean {
  const variables = actionMutation.variables.value;
  return (
    actionMutation.isPending.value &&
    variables?.pluginId === plugin.id &&
    variables?.action === action
  );
}

function runAction(plugin: PluginSummary, action: PluginAction) {
  actionMutation.mutate({ pluginId: plugin.id, action });
}

function actionDisabled(plugin: PluginSummary, action: PluginAction): boolean {
  return actionMutation.isPending.value || !canAction(plugin, action);
}

function listValue(items: string[] | undefined): string {
  return items?.length ? items.join(", ") : "-";
}

function boolValue(value: boolean | undefined): string {
  return value ? "enabled" : "disabled";
}

function toolName(tool: Record<string, string>, index: number): string {
  return tool.name || tool.id || `tool-${index + 1}`;
}

function schemaKeys(plugin: PluginSummary): string[] {
  return Object.keys(plugin.config_schema ?? {});
}
</script>

<template>
  <div class="plugins-page">
    <Alert v-if="error" variant="destructive">
      Failed to load plugins: {{ error.message }}
    </Alert>

    <section class="plugins-toolbar">
      <label class="search-control">
        <Search :size="15" />
        <input v-model="search" type="search" placeholder="Search plugins" />
      </label>
      <select v-model="stateFilter" class="state-select">
        <option value="all">All states</option>
        <option v-for="state in states" :key="state" :value="state">
          {{ stateLabel(state) }}
        </option>
      </select>
      <Button
        variant="outline"
        size="sm"
        :disabled="isFetching"
        @click="refetch()"
      >
        <RefreshCw :size="14" />
        Refresh
      </Button>
    </section>

    <section class="state-grid">
      <Card v-for="state in states" :key="state" class="state-card">
        <span class="state-card-label">{{ stateLabel(state) }}</span>
        <strong>{{ stateCounts[state] }}</strong>
      </Card>
    </section>

    <div v-if="isLoading && !pluginData" class="loading">Loading...</div>

    <div v-else class="plugins-workspace">
      <aside class="plugin-list">
        <button
          v-for="plugin in filteredPlugins"
          :key="plugin.id"
          class="plugin-row"
          :class="{ active: selectedId === plugin.id }"
          @click="selectedId = plugin.id"
        >
          <span class="plugin-row-main">
            <span class="plugin-row-title">{{ plugin.name }}</span>
            <Badge :variant="stateVariant(plugin.state)">
              {{ stateLabel(plugin.state) }}
            </Badge>
          </span>
          <span class="plugin-row-id">{{ plugin.id }}</span>
          <span class="plugin-row-meta">
            {{ plugin.version }} · {{ plugin.load_phase }}
          </span>
        </button>
        <div v-if="!filteredPlugins.length" class="empty-list">
          No plugins matched.
        </div>
      </aside>

      <Card v-if="selectedPlugin" class="plugin-detail">
        <header class="detail-header">
          <div class="detail-title">
            <h2>{{ selectedPlugin.name }}</h2>
            <div class="detail-meta">
              <code>{{ selectedPlugin.id }}</code>
              <Badge :variant="stateVariant(selectedPlugin.state)">
                {{ stateLabel(selectedPlugin.state) }}
              </Badge>
              <Badge variant="outline">{{ selectedPlugin.load_phase }}</Badge>
            </div>
          </div>
          <div class="detail-actions">
            <Button
              v-if="canAction(selectedPlugin, 'load')"
              size="sm"
              variant="outline"
              :disabled="actionDisabled(selectedPlugin, 'load')"
              @click="runAction(selectedPlugin, 'load')"
            >
              <Archive :size="14" />
              {{ isPending(selectedPlugin, "load") ? "Loading" : "Load" }}
            </Button>
            <Button
              v-if="canAction(selectedPlugin, 'enable')"
              size="sm"
              :disabled="actionDisabled(selectedPlugin, 'enable')"
              @click="runAction(selectedPlugin, 'enable')"
            >
              <Power :size="14" />
              {{ isPending(selectedPlugin, "enable") ? "Enabling" : "Enable" }}
            </Button>
            <Button
              v-if="canAction(selectedPlugin, 'disable')"
              size="sm"
              variant="outline"
              :disabled="actionDisabled(selectedPlugin, 'disable')"
              @click="runAction(selectedPlugin, 'disable')"
            >
              <PowerOff :size="14" />
              {{ isPending(selectedPlugin, "disable") ? "Disabling" : "Disable" }}
            </Button>
            <Button
              v-if="canAction(selectedPlugin, 'reload')"
              size="sm"
              variant="outline"
              :disabled="actionDisabled(selectedPlugin, 'reload')"
              @click="runAction(selectedPlugin, 'reload')"
            >
              <RefreshCw :size="14" />
              {{ isPending(selectedPlugin, "reload") ? "Reloading" : "Reload" }}
            </Button>
            <Button
              v-if="canAction(selectedPlugin, 'unload')"
              size="sm"
              variant="destructive"
              :disabled="actionDisabled(selectedPlugin, 'unload')"
              @click="runAction(selectedPlugin, 'unload')"
            >
              <Play :size="14" />
              {{ isPending(selectedPlugin, "unload") ? "Unloading" : "Unload" }}
            </Button>
          </div>
        </header>

        <Alert v-if="selectedPlugin.error_message" variant="destructive">
          {{ selectedPlugin.error_message }}
        </Alert>

        <p v-if="selectedPlugin.description" class="plugin-description">
          {{ selectedPlugin.description }}
        </p>

        <section class="detail-section">
          <h3>Runtime</h3>
          <dl class="kv-list">
            <div>
              <dt>Entrypoint</dt>
              <dd><code>{{ selectedPlugin.entrypoint }}</code></dd>
            </div>
            <div>
              <dt>Path</dt>
              <dd><code>{{ selectedPlugin.path }}</code></dd>
            </div>
            <div>
              <dt>Instance</dt>
              <dd>{{ boolValue(selectedPlugin.has_instance) }}</dd>
            </div>
            <div>
              <dt>API bridge</dt>
              <dd>{{ boolValue(selectedPlugin.has_runtime_api) }}</dd>
            </div>
          </dl>
        </section>

        <section class="detail-section">
          <h3>Permissions</h3>
          <dl class="kv-list permission-list">
            <div>
              <dt>Network inbound</dt>
              <dd>{{ boolValue(selectedPlugin.permissions.network?.inbound) }}</dd>
            </div>
            <div>
              <dt>Network outbound</dt>
              <dd>{{ listValue(selectedPlugin.permissions.network?.outbound) }}</dd>
            </div>
            <div>
              <dt>Filesystem read</dt>
              <dd>{{ listValue(selectedPlugin.permissions.filesystem?.read) }}</dd>
            </div>
            <div>
              <dt>Filesystem write</dt>
              <dd>{{ listValue(selectedPlugin.permissions.filesystem?.write) }}</dd>
            </div>
            <div>
              <dt>Memory</dt>
              <dd>
                read {{ boolValue(selectedPlugin.permissions.memory?.read) }},
                write {{ boolValue(selectedPlugin.permissions.memory?.write) }}
              </dd>
            </div>
            <div>
              <dt>System</dt>
              <dd>
                env {{ listValue(selectedPlugin.permissions.system?.env_vars) }},
                subprocess {{ boolValue(selectedPlugin.permissions.system?.subprocess) }},
                signals {{ boolValue(selectedPlugin.permissions.system?.signal_handlers) }}
              </dd>
            </div>
            <div>
              <dt>LLM access</dt>
              <dd>{{ boolValue(selectedPlugin.permissions.llm_access) }}</dd>
            </div>
          </dl>
        </section>

        <section class="detail-section">
          <h3>Capabilities</h3>
          <div class="badge-group">
            <Badge
              v-for="(tool, index) in selectedPlugin.capabilities.tools ?? []"
              :key="`${selectedPlugin.id}-tool-${index}`"
              variant="default"
            >
              {{ toolName(tool, index) }}
            </Badge>
            <Badge
              v-for="eventName in selectedPlugin.capabilities.subscribes_to ?? []"
              :key="`${selectedPlugin.id}-event-${eventName}`"
              variant="outline"
            >
              {{ eventName }}
            </Badge>
            <span
              v-if="
                !(selectedPlugin.capabilities.tools ?? []).length &&
                !(selectedPlugin.capabilities.subscribes_to ?? []).length
              "
              class="muted"
            >
              -
            </span>
          </div>
        </section>

        <section class="detail-section">
          <h3>Configuration</h3>
          <dl class="kv-list">
            <div>
              <dt>Configured</dt>
              <dd>{{ boolValue(selectedPlugin.has_config) }}</dd>
            </div>
            <div>
              <dt>Keys</dt>
              <dd>
                <span v-if="selectedPlugin.config_keys.length" class="badge-group">
                  <Badge
                    v-for="key in selectedPlugin.config_keys"
                    :key="`${selectedPlugin.id}-config-${key}`"
                    variant="outline"
                  >
                    {{ key }}
                  </Badge>
                </span>
                <span v-else>-</span>
              </dd>
            </div>
            <div>
              <dt>Schema keys</dt>
              <dd>
                <span v-if="schemaKeys(selectedPlugin).length" class="badge-group">
                  <Badge
                    v-for="key in schemaKeys(selectedPlugin)"
                    :key="`${selectedPlugin.id}-schema-${key}`"
                    variant="secondary"
                  >
                    {{ key }}
                  </Badge>
                </span>
                <span v-else>-</span>
              </dd>
            </div>
          </dl>
        </section>

        <section v-if="selectedPlugin.depends_on.length" class="detail-section">
          <h3>Dependencies</h3>
          <div class="badge-group">
            <Badge
              v-for="dep in selectedPlugin.depends_on"
              :key="`${selectedPlugin.id}-dep-${dep.id}`"
              variant="outline"
            >
              {{ dep.id }} {{ dep.version }}
            </Badge>
          </div>
        </section>
      </Card>

      <Card v-else class="plugin-detail empty-detail">
        No plugins available.
      </Card>
    </div>
  </div>
</template>

<style scoped>
.plugins-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.plugins-toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.search-control {
  min-width: min(22rem, 100%);
  flex: 1;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  border: 1px solid var(--color-input);
  border-radius: var(--radius-md);
  background: var(--color-card);
  color: var(--color-muted-foreground);
  padding: 0 0.625rem;
}

.search-control input {
  width: 100%;
  min-width: 0;
  border: 0;
  background: transparent;
  color: var(--color-foreground);
  font-size: 0.8125rem;
  outline: none;
}

.state-select {
  height: 34px;
  border: 1px solid var(--color-input);
  border-radius: var(--radius-md);
  background: var(--color-card);
  color: var(--color-foreground);
  font-size: 0.8125rem;
  padding: 0 0.5rem;
}

.state-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0.75rem;
}

.state-card {
  min-height: 68px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.state-card-label {
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.state-card strong {
  font-size: 1.25rem;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.loading,
.muted,
.empty-list,
.empty-detail {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.plugins-workspace {
  display: grid;
  grid-template-columns: minmax(18rem, 0.34fr) minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
}

.plugin-list {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 0;
}

.plugin-row {
  width: 100%;
  min-height: 82px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-card);
  color: var(--color-foreground);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  padding: 0.75rem;
  text-align: left;
  transition:
    background 0.15s,
    border-color 0.15s,
    box-shadow 0.15s;
}

.plugin-row:hover,
.plugin-row.active {
  border-color: color-mix(in srgb, var(--color-primary) 38%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-card));
}

.plugin-row.active {
  box-shadow: inset 3px 0 0 var(--color-primary);
}

.plugin-row-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
}

.plugin-row-title {
  min-width: 0;
  font-size: 0.875rem;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.plugin-row-id,
.plugin-row-meta {
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-list {
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  padding: 1.5rem;
  text-align: center;
}

.plugin-detail {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 1rem;
}

.detail-title {
  min-width: 0;
}

.detail-title h2 {
  margin: 0;
  font-size: 1.05rem;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.detail-meta,
.detail-actions,
.badge-group {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.detail-meta {
  margin-top: 0.5rem;
}

.detail-actions {
  justify-content: flex-end;
}

.plugin-description {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  line-height: 1.6;
  margin: 0;
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.detail-section h3 {
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  margin: 0;
  text-transform: uppercase;
}

.kv-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: 0;
  border-top: 1px solid var(--color-border);
}

.kv-list > div {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(8rem, 0.35fr) minmax(0, 1fr);
  gap: 0.75rem;
  border-bottom: 1px solid var(--color-border-subtle);
  padding: 0.625rem 0;
}

.permission-list > div {
  grid-column: 1 / -1;
}

.kv-list dt {
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

.kv-list dd {
  min-width: 0;
  color: var(--color-foreground);
  font-size: 0.8125rem;
  margin: 0;
  overflow-wrap: anywhere;
}

code {
  background: var(--color-muted);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 0.72rem;
  padding: 0.0625rem 0.3125rem;
}

@media (max-width: 1100px) {
  .state-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .plugins-workspace {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .state-grid,
  .kv-list {
    grid-template-columns: 1fr;
  }

  .detail-header {
    flex-direction: column;
  }

  .detail-actions {
    justify-content: flex-start;
  }

  .kv-list > div {
    grid-template-columns: 1fr;
    gap: 0.25rem;
  }
}
</style>
