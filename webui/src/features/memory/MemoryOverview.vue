<script setup lang="ts">
import { computed } from "vue";
import { RefreshCw } from "lucide-vue-next";
import { useMemoryOverview } from "@/api/queries";
import type { MemoryScopeSummary } from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import { formatDateTime } from "@/lib/utils";

const props = defineProps<{ scopeType: string; scopeId: string }>();
const emit = defineEmits<{ selectScope: [scope: MemoryScopeSummary] }>();
const query = useMemoryOverview(computed(() => ({ scope_type: props.scopeType.trim(), scope_id: props.scopeId.trim() })));
const summary = computed(() => query.data.value);
const metrics = computed(() => summary.value ? [
  { label: "Active memories", value: summary.value.active },
  { label: "Archived", value: summary.value.archived },
  { label: "Restricted", value: summary.value.restricted },
  { label: "Pending candidates", value: summary.value.candidates.pending ?? 0 },
  { label: "No subject field", value: summary.value.without_subject },
  { label: "No evidence references", value: summary.value.without_evidence },
] : []);
</script>

<template>
  <section class="overview" aria-label="Memory storage overview">
    <div class="overview-heading">
      <div><strong>Memory state</strong><p>Totals for the selected scope, independent of search and category filters.</p></div>
      <Button size="icon" variant="outline" title="Refresh memory overview" :disabled="query.isFetching.value" @click="query.refetch()"><RefreshCw :size="15" /></Button>
    </div>
    <Alert v-if="query.error.value" variant="destructive">Failed to load overview: {{ query.error.value.message }}</Alert>
    <p v-else-if="query.isLoading.value">Loading memory state...</p>
    <template v-else-if="summary">
      <div class="metrics"><div v-for="metric in metrics" :key="metric.label" class="metric"><span>{{ metric.label }}</span><strong>{{ metric.value.toLocaleString() }}</strong></div></div>
      <p>Subject and evidence counts describe missing stored fields, not confirmed mistakes. Restricted and missing-field counts cover active memories.</p>
      <details>
        <summary>Browse scopes · {{ summary.scopes.length }}{{ summary.scopes_truncated ? '+' : '' }}</summary>
        <div class="scope-list">
          <button v-for="scope in summary.scopes" :key="`${scope.scope_type}:${scope.scope_id}`" type="button" @click="emit('selectScope', scope)">
            <span><b>{{ scope.scope_type }}</b><code>{{ scope.scope_id }}</code></span><span>{{ scope.active }} active · {{ scope.restricted }} restricted</span>
          </button>
          <p v-if="!summary.scopes.length">No active memories in this scope.</p>
        </div>
        <p v-if="summary.scopes_truncated">Showing the 50 largest scopes. Use the scope filters to inspect others.</p>
      </details>
      <div class="policy-note">
        <span>Public cross-scope recall: <b>{{ summary.soft_scope ? 'enabled' : 'disabled' }}</b></span>
        <span v-if="summary.last_updated_at">Last change: {{ formatDateTime(summary.last_updated_at) }}</span>
      </div>
      <p>This is an operator view of stored memory, not a replay of a model's context. Memory audience rules and chat-history access domains are separate checks; public alone does not mean visible in every conversation.</p>
    </template>
  </section>
</template>

<style scoped>
.overview { padding: 1rem; border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-card); display: grid; gap: 0.8rem; }
.overview-heading { display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start; }
.overview-heading strong { font-size: 0.9rem; }
p { margin: 0.2rem 0 0; font-size: 0.75rem; color: var(--color-muted-foreground); line-height: 1.6; }
.metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 0.6rem; }
.metric { padding: 0.75rem; background: var(--color-background); border: 1px solid var(--color-border); border-radius: var(--radius-md); display: grid; gap: 0.4rem; }
.metric span { color: var(--color-muted-foreground); font-size: 0.7rem; }
.metric strong { font-size: 1.45rem; font-weight: 600; font-variant-numeric: tabular-nums; }
summary { cursor: pointer; font-size: 0.8rem; }
.scope-list { display: grid; gap: 0.35rem; margin-top: 0.65rem; max-height: 16rem; overflow: auto; }
.scope-list button { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; padding: 0.6rem; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-background); color: var(--color-foreground); text-align: left; cursor: pointer; font-size: 0.75rem; }
.scope-list button:hover { background: var(--color-accent); }
.scope-list span:first-child { display: flex; gap: 0.5rem; min-width: 0; }
code { overflow-wrap: anywhere; }
.policy-note { display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; font-size: 0.75rem; }
@media (max-width: 1150px) { .metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 600px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
