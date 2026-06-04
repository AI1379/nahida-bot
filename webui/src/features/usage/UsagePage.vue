<script setup lang="ts">
import { ref, computed } from "vue";
import { useTokenStats, useTokenEvents, useTokenClear } from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import { formatNumber, formatTime } from "@/lib/utils";

const days = ref(7);
const selectedProvider = ref("");

const { data: stats, isLoading: statsLoading, error: statsError } = useTokenStats(
  computed(() => ({
    days: days.value,
    provider_id: selectedProvider.value || undefined,
  })),
);

const { data: eventsData, isLoading: eventsLoading } = useTokenEvents(
  computed(() => ({
    limit: 50,
    provider_id: selectedProvider.value || undefined,
  })),
);

function formatCost(cost: number | null): string {
  if (cost === null || cost === undefined) return "—";
  return `$${cost.toFixed(4)}`;
}

function formatTokens(n: number): string {
  return formatNumber(n);
}

const hasData = computed(() => {
  return (stats.value?.totals.event_count ?? 0) > 0;
});

const showClearDialog = ref(false);
const clearMutation = useTokenClear();

function executeClear() {
  clearMutation.mutate(undefined, {
    onSettled: () => { showClearDialog.value = false; },
  });
}

const maxDailyTokens = computed(() => {
  if (!stats.value?.daily?.length) return 1;
  return Math.max(
    ...stats.value.daily.map((d) => Math.max(d.input_tokens, d.output_tokens)),
    1,
  );
});
</script>

<template>
  <div class="usage-page">
    <Alert v-if="statsError" variant="destructive">
      Failed to load token stats: {{ statsError.message }}
    </Alert>

    <!-- Filters -->
    <section class="section">
      <div class="filters-row">
        <div class="filter-group">
          <label class="filter-label">Range</label>
          <select v-model.number="days" class="filter-select">
            <option :value="1">Last 24 hours</option>
            <option :value="7">Last 7 days</option>
            <option :value="30">Last 30 days</option>
            <option :value="90">Last 90 days</option>
          </select>
        </div>
        <div class="filter-group">
          <label class="filter-label">Provider</label>
          <select v-model="selectedProvider" class="filter-select">
            <option value="">All Providers</option>
            <option
              v-for="p in stats?.by_provider ?? []"
              :key="p.provider_id"
              :value="p.provider_id"
            >
              {{ p.provider_id }}
            </option>
          </select>
        </div>
        <div class="filter-group filter-group-end">
          <Button
            v-if="hasData"
            variant="destructive"
            size="sm"
            @click="showClearDialog = true"
          >
            Clear History
          </Button>
        </div>
      </div>
    </section>

    <!-- Loading -->
    <div v-if="statsLoading && !stats" class="loading">Loading...</div>

    <template v-if="stats">
      <!-- Aggregate totals -->
      <section class="section">
        <h2 class="section-title">Totals</h2>
        <div class="grid grid-5">
          <Card>
            <div class="metric-label">Input</div>
            <div class="metric-value">{{ formatTokens(stats.totals.input_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Output</div>
            <div class="metric-value">{{ formatTokens(stats.totals.output_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Cached</div>
            <div class="metric-value">{{ formatTokens(stats.totals.cached_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Reasoning</div>
            <div class="metric-value">{{ formatTokens(stats.totals.reasoning_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Est. Cost</div>
            <div class="metric-value">{{ formatCost(stats.totals.estimated_cost) }}</div>
            <div class="metric-sub">{{ stats.totals.event_count }} events</div>
          </Card>
        </div>
      </section>

      <!-- Per-Provider Breakdown -->
      <section v-if="stats.by_provider.length > 0" class="section">
        <h2 class="section-title">By Provider</h2>
        <Card>
          <div class="provider-table-wrap">
            <table class="provider-table">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Model</th>
                  <th class="num">Input</th>
                  <th class="num">Output</th>
                  <th class="num">Cached</th>
                  <th class="num">Cost</th>
                  <th class="num">Events</th>
                  <th class="center">Est.</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in stats.by_provider" :key="p.provider_id + p.model">
                  <td><Badge variant="outline">{{ p.provider_id }}</Badge></td>
                  <td class="model-cell">{{ p.model }}</td>
                  <td class="num">{{ formatTokens(p.input_tokens) }}</td>
                  <td class="num">{{ formatTokens(p.output_tokens) }}</td>
                  <td class="num">{{ formatTokens(p.cached_tokens) }}</td>
                  <td class="num">{{ formatCost(p.estimated_cost) }}</td>
                  <td class="num">{{ p.event_count }}</td>
                  <td class="center">
                    <span v-if="p.estimated" title="Estimated tokens">~</span>
                    <span v-else style="color: var(--color-success)">✓</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      <!-- Daily chart (simple bar chart) -->
      <section v-if="stats.daily.length > 0" class="section">
        <h2 class="section-title">Daily Usage</h2>
        <Card>
          <div class="chart-container">
            <div class="chart-bars">
              <div
                v-for="d in stats.daily"
                :key="d.date"
                class="chart-bar-group"
                :title="`${d.date}: in=${formatTokens(d.input_tokens)} out=${formatTokens(d.output_tokens)}`"
              >
                <div class="bar-stack">
                  <div
                    class="bar bar-input"
                    :style="{ height: `${(d.input_tokens / maxDailyTokens) * 100}%` }"
                  ></div>
                  <div
                    class="bar bar-output"
                    :style="{ height: `${(d.output_tokens / maxDailyTokens) * 100}%` }"
                  ></div>
                </div>
                <span class="bar-label">{{ d.date.slice(5) }}</span>
              </div>
            </div>
            <div class="chart-legend">
              <span class="legend-item"><span class="legend-swatch input"></span> Input</span>
              <span class="legend-item"><span class="legend-swatch output"></span> Output</span>
            </div>
          </div>
        </Card>
      </section>

      <!-- No data -->
      <section v-if="!hasData && !statsLoading" class="section">
        <Card>
          <div class="empty-state">
            <p>No token usage data recorded yet.</p>
            <p class="empty-hint">
              Token usage will appear here automatically when the bot processes messages.
            </p>
          </div>
        </Card>
      </section>

      <!-- Recent events -->
      <section v-if="eventsData?.events?.length" class="section">
        <h2 class="section-title">Recent Events</h2>
        <Card>
          <div class="events-table-wrap">
            <table class="events-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Provider</th>
                  <th>Model</th>
                  <th class="num">Input</th>
                  <th class="num">Output</th>
                  <th class="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(ev, i) in eventsData.events"
                  :key="ev.id ?? i"
                >
                  <td class="time-cell">{{ formatTime(ev.timestamp) }}</td>
                  <td>{{ ev.provider_id }}</td>
                  <td class="model-cell">{{ ev.model }}</td>
                  <td class="num">{{ formatTokens(ev.input_tokens) }}</td>
                  <td class="num">{{ formatTokens(ev.output_tokens) }}</td>
                  <td class="num">{{ formatCost(ev.estimated_cost) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="eventsLoading" class="events-loading">Refreshing...</div>
        </Card>
      </section>
    </template>

    <!-- Clear history confirm -->
    <ConfirmDialog
      v-model:open="showClearDialog"
      title="Clear Token Usage History"
      description="This will permanently delete all token usage records. This action cannot be undone."
      variant="destructive"
      confirm-label="Clear All"
      :loading="clearMutation.isPending.value"
      @confirm="executeClear"
    />
  </div>
</template>

<style scoped>
.usage-page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.loading {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.section-title {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted-foreground);
  margin: 0 0 0.5rem;
}

/* Filters */
.filters-row {
  display: flex;
  gap: 1rem;
  align-items: flex-end;
}

.filter-group-end {
  margin-left: auto;
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.filter-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-muted-foreground);
}

.filter-select {
  padding: 0.375rem 0.625rem;
  font-size: 0.8125rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-card);
  color: var(--color-foreground);
  cursor: pointer;
}

/* Grid */
.grid {
  display: grid;
  gap: 0.75rem;
}

.grid-5 {
  grid-template-columns: repeat(5, 1fr);
}

@media (max-width: 1000px) {
  .grid-5 {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 600px) {
  .grid-5 {
    grid-template-columns: repeat(2, 1fr);
  }
}

.metric-label {
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 0.25rem;
}

.metric-value {
  font-size: 1.125rem;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.metric-sub {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  margin-top: 0.125rem;
}

/* Provider table */
.provider-table-wrap {
  overflow-x: auto;
}

.provider-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.provider-table th {
  text-align: left;
  font-weight: 600;
  color: var(--color-muted-foreground);
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.provider-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border-subtle);
  vertical-align: middle;
}

.provider-table .num,
.provider-table th.num {
  text-align: right;
}

.provider-table .center,
.provider-table th.center {
  text-align: center;
}

.model-cell {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Chart */
.chart-container {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.chart-bars {
  display: flex;
  align-items: flex-end;
  gap: 0.25rem;
  height: 140px;
  padding-top: 0.5rem;
}

.chart-bar-group {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}

.bar-stack {
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  border-radius: 2px 2px 0 0;
  overflow: hidden;
}

.bar {
  width: 100%;
  min-height: 2px;
  transition: height 0.2s;
}

.bar-input {
  background: var(--color-primary);
  opacity: 0.7;
}

.bar-output {
  background: var(--color-primary);
  opacity: 0.35;
}

.bar-label {
  font-size: 0.55rem;
  color: var(--color-muted-foreground);
  margin-top: 0.25rem;
}

.chart-legend {
  display: flex;
  gap: 1rem;
  justify-content: center;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
}

.legend-swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.legend-swatch.input {
  background: var(--color-primary);
  opacity: 0.7;
}

.legend-swatch.output {
  background: var(--color-primary);
  opacity: 0.35;
}

/* Events table */
.events-table-wrap {
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}

.events-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.events-table th {
  text-align: left;
  font-weight: 600;
  color: var(--color-muted-foreground);
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  position: sticky;
  top: 0;
  background: var(--color-card);
}

.events-table td {
  padding: 0.375rem 0.75rem;
  border-bottom: 1px solid var(--color-border-subtle);
}

.events-table .num,
.events-table th.num {
  text-align: right;
}

.time-cell {
  font-variant-numeric: tabular-nums;
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.events-loading {
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
  padding: 0.5rem 0.75rem;
}

/* Empty */
.empty-state {
  text-align: center;
  padding: 2rem 1rem;
  color: var(--color-muted-foreground);
}

.empty-hint {
  font-size: 0.75rem;
  margin-top: 0.5rem;
  opacity: 0.7;
}
</style>
