<script setup lang="ts">
import { ref, computed } from "vue";
import { useCronList } from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import type { CronJob } from "@/api/schemas";
import { relativeTime } from "@/lib/utils";

const activeFilter = ref("all");
const filterOptions = [
  { id: "all", label: "All" },
  { id: "true", label: "Active" },
  { id: "false", label: "Inactive" },
];

const filterRef = computed(() => ({
  active: activeFilter.value,
}));

const { data, isLoading, error } = useCronList(filterRef);

function statusVariant(job: CronJob) {
  if (!job.is_active) return "secondary";
  if (job.failure_count > 0) return "warning";
  return "success";
}

function statusLabel(job: CronJob) {
  if (!job.is_active) return "inactive";
  if (job.failure_count > 0) return "failing";
  return "active";
}

function modeLabel(mode: string) {
  switch (mode) {
    case "once": return "Once";
    case "interval": return "Interval";
    case "cron": return "Cron";
    default: return mode;
  }
}
</script>

<template>
  <div class="cron-page">
    <Alert v-if="error" variant="destructive">
      Failed to load CRON jobs: {{ error.message }}
    </Alert>

    <!-- Filters -->
    <div class="cron-toolbar">
      <div class="filter-group">
        <Button
          v-for="opt in filterOptions"
          :key="opt.id"
          :variant="activeFilter === opt.id ? 'default' : 'outline'"
          size="sm"
          @click="activeFilter = opt.id"
        >
          {{ opt.label }}
        </Button>
      </div>
      <span v-if="data" class="job-count">{{ data.jobs.length }} jobs</span>
    </div>

    <div v-if="isLoading" class="loading">Loading...</div>

    <!-- Table -->
    <Card v-if="data && data.jobs.length" class="cron-table-card">
      <table class="cron-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Target</th>
            <th>Mode</th>
            <th>Session</th>
            <th>Prompt</th>
            <th>Status</th>
            <th>Next Fire</th>
            <th>Runs</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in data.jobs" :key="job.job_id">
            <td class="mono">{{ job.job_id.slice(0, 8) }}</td>
            <td>
              <div>{{ job.platform }}</div>
              <div class="sub">{{ job.chat_type }}:{{ job.chat_id }}</div>
            </td>
            <td>
              <Badge variant="outline">{{ modeLabel(job.mode) }}</Badge>
            </td>
            <td>
              <Badge variant="secondary">{{ job.session_mode }}</Badge>
            </td>
            <td class="prompt-cell">{{ job.prompt.slice(0, 60) }}{{ job.prompt.length > 60 ? "..." : "" }}</td>
            <td>
              <Badge :variant="statusVariant(job)">{{ statusLabel(job) }}</Badge>
            </td>
            <td class="mono">{{ job.next_fire_at ? relativeTime(job.next_fire_at) : "-" }}</td>
            <td>{{ job.run_count }}{{ job.max_runs ? `/${job.max_runs}` : "" }}</td>
            <td class="mono">{{ relativeTime(job.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </Card>

    <div v-if="data && !data.jobs.length" class="empty">
      No CRON jobs found.
    </div>
  </div>
</template>

<style scoped>
.cron-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.cron-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.filter-group {
  display: flex;
  gap: 0.375rem;
}

.job-count {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.cron-table-card {
  padding: 0;
  overflow: hidden;
}

.cron-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.cron-table th {
  text-align: left;
  padding: 0.625rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  font-weight: 600;
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.cron-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
}

.cron-table tr:last-child td {
  border-bottom: none;
}

.mono {
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.sub {
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
}

.prompt-cell {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  text-align: center;
  padding: 2rem;
}
</style>
