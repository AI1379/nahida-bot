<script setup lang="ts">
import { ref, computed } from "vue";
import {
  useCronList,
  useCronCreate,
  useCronUpdate,
  useCronActivate,
  useCronCancel,
  useCronDelete,
} from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import CronJobFormDialog from "./CronJobFormDialog.vue";
import type { CronJob } from "@/api/schemas";
import { relativeTime } from "@/lib/utils";
import {
  Pencil,
  Ban,
  Trash2,
  Plus,
  Clock,
  Hash,
  Target,
  MessageSquare,
  Play,
  ArrowUp,
  ArrowDown,
  ChevronsUpDown,
  User,
} from "lucide-vue-next";

type SortKey = "next_fire_at" | "last_fired_at" | "target" | "mode" | "session_mode" | "status";
type SortDirection = "asc" | "desc";

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
const sortState = ref<{ key: SortKey; direction: SortDirection } | null>(null);

// Mutations
const createMutation = useCronCreate();
const updateMutation = useCronUpdate();
const activateMutation = useCronActivate();
const cancelMutation = useCronCancel();
const deleteMutation = useCronDelete();

// Form dialog
const showFormDialog = ref(false);
const editingJob = ref<CronJob | null>(null);
const formRef = ref<InstanceType<typeof CronJobFormDialog> | null>(null);
const formLoading = computed(() => createMutation.isPending.value || updateMutation.isPending.value);

// Cancel confirm
const showCancelDialog = ref(false);
const cancelJobId = ref("");
const cancelJobLabel = ref("");

// Activate confirm
const showActivateDialog = ref(false);
const activateJobId = ref("");
const activateJobLabel = ref("");

// Delete confirm
const showDeleteDialog = ref(false);
const deleteJobId = ref("");
const deleteJobLabel = ref("");

function openCreate() {
  editingJob.value = null;
  showFormDialog.value = true;
}

function openEdit(job: CronJob) {
  editingJob.value = job;
  showFormDialog.value = true;
}

function handleFormSubmit() {
  if (!formRef.value) return;
  if (editingJob.value) {
    const payload = formRef.value.buildUpdatePayload();
    updateMutation.mutate(
      { jobId: editingJob.value.job_id, data: payload },
      { onSuccess: () => { showFormDialog.value = false; } },
    );
  } else {
    const payload = formRef.value.buildCreatePayload();
    createMutation.mutate(payload, {
      onSuccess: () => { showFormDialog.value = false; },
    });
  }
}

function confirmActivate(job: CronJob) {
  activateJobId.value = job.job_id;
  activateJobLabel.value = job.job_id.slice(0, 8);
  showActivateDialog.value = true;
}

function confirmCancel(job: CronJob) {
  cancelJobId.value = job.job_id;
  cancelJobLabel.value = job.job_id.slice(0, 8);
  showCancelDialog.value = true;
}

function confirmDelete(job: CronJob) {
  deleteJobId.value = job.job_id;
  deleteJobLabel.value = job.job_id.slice(0, 8);
  showDeleteDialog.value = true;
}

function executeCancel() {
  cancelMutation.mutate(cancelJobId.value, {
    onSettled: () => { showCancelDialog.value = false; },
  });
}

function executeActivate() {
  activateMutation.mutate(activateJobId.value, {
    onSettled: () => { showActivateDialog.value = false; },
  });
}

function executeDelete() {
  deleteMutation.mutate(deleteJobId.value, {
    onSettled: () => { showDeleteDialog.value = false; },
  });
}

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

function targetLabel(job: CronJob) {
  return `${job.platform}:${job.chat_type || "unknown"}:${job.chat_id}`;
}

function toggleSort(key: SortKey) {
  if (!sortState.value || sortState.value.key !== key) {
    sortState.value = { key, direction: "asc" };
    return;
  }
  if (sortState.value.direction === "asc") {
    sortState.value = { key, direction: "desc" };
    return;
  }
  sortState.value = null;
}

function isSorted(key: SortKey, direction: SortDirection) {
  return sortState.value?.key === key && sortState.value.direction === direction;
}

function sortAriaLabel(label: string, key: SortKey) {
  if (!sortState.value || sortState.value.key !== key) return `Sort by ${label} ascending`;
  if (sortState.value.direction === "asc") return `Sort by ${label} descending`;
  return `Clear ${label} sorting`;
}

function timestampValue(value: string | null | undefined) {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : null;
}

function sortValue(job: CronJob, key: SortKey): string | number | null {
  switch (key) {
    case "next_fire_at": return timestampValue(job.next_fire_at);
    case "last_fired_at": return timestampValue(job.last_fired_at);
    case "target": return targetLabel(job).toLowerCase();
    case "mode": return job.mode;
    case "session_mode": return job.session_mode;
    case "status": return statusLabel(job);
  }
}

function compareJobs(a: CronJob, b: CronJob, key: SortKey, direction: SortDirection) {
  const av = sortValue(a, key);
  const bv = sortValue(b, key);
  const aEmpty = av === null || av === "";
  const bEmpty = bv === null || bv === "";
  if (aEmpty && bEmpty) return 0;
  if (aEmpty) return 1;
  if (bEmpty) return -1;

  const result = typeof av === "number" && typeof bv === "number"
    ? av - bv
    : String(av).localeCompare(String(bv));
  return direction === "asc" ? result : -result;
}

const sortedJobs = computed(() => {
  const jobs = data.value?.jobs ?? [];
  if (!sortState.value) return jobs;
  const { key, direction } = sortState.value;
  return jobs
    .map((job, index) => ({ job, index }))
    .sort((a, b) => compareJobs(a.job, b.job, key, direction) || a.index - b.index)
    .map((entry) => entry.job);
});
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
      <div class="toolbar-right">
        <span v-if="data" class="job-count">{{ data.jobs.length }} jobs</span>
        <Button size="sm" @click="openCreate">
          <Plus :size="14" />
          New Job
        </Button>
      </div>
    </div>

    <div v-if="isLoading" class="loading">Loading...</div>

    <!-- Desktop: table view -->
    <Card v-if="data && data.jobs.length" class="cron-table-card desktop-only">
      <div class="table-scroll">
        <table class="cron-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>
                <button class="sort-button" type="button" :aria-label="sortAriaLabel('target', 'target')" @click="toggleSort('target')">
                  <span>Target</span>
                  <ArrowUp v-if="isSorted('target', 'asc')" :size="12" />
                  <ArrowDown v-else-if="isSorted('target', 'desc')" :size="12" />
                  <ChevronsUpDown v-else :size="12" />
                </button>
              </th>
              <th>
                <button class="sort-button" type="button" :aria-label="sortAriaLabel('type', 'mode')" @click="toggleSort('mode')">
                  <span>Type</span>
                  <ArrowUp v-if="isSorted('mode', 'asc')" :size="12" />
                  <ArrowDown v-else-if="isSorted('mode', 'desc')" :size="12" />
                  <ChevronsUpDown v-else :size="12" />
                </button>
              </th>
              <th>
                <button class="sort-button" type="button" :aria-label="sortAriaLabel('session mode', 'session_mode')" @click="toggleSort('session_mode')">
                  <span>Session</span>
                  <ArrowUp v-if="isSorted('session_mode', 'asc')" :size="12" />
                  <ArrowDown v-else-if="isSorted('session_mode', 'desc')" :size="12" />
                  <ChevronsUpDown v-else :size="12" />
                </button>
              </th>
              <th>Prompt</th>
              <th>
                <button class="sort-button" type="button" :aria-label="sortAriaLabel('status', 'status')" @click="toggleSort('status')">
                  <span>Status</span>
                  <ArrowUp v-if="isSorted('status', 'asc')" :size="12" />
                  <ArrowDown v-else-if="isSorted('status', 'desc')" :size="12" />
                  <ChevronsUpDown v-else :size="12" />
                </button>
              </th>
              <th>
                <button class="sort-button" type="button" :aria-label="sortAriaLabel('next fire', 'next_fire_at')" @click="toggleSort('next_fire_at')">
                  <span>Next Fire</span>
                  <ArrowUp v-if="isSorted('next_fire_at', 'asc')" :size="12" />
                  <ArrowDown v-else-if="isSorted('next_fire_at', 'desc')" :size="12" />
                  <ChevronsUpDown v-else :size="12" />
                </button>
              </th>
              <th>
                <button class="sort-button" type="button" :aria-label="sortAriaLabel('last fire', 'last_fired_at')" @click="toggleSort('last_fired_at')">
                  <span>Last Fire</span>
                  <ArrowUp v-if="isSorted('last_fired_at', 'asc')" :size="12" />
                  <ArrowDown v-else-if="isSorted('last_fired_at', 'desc')" :size="12" />
                  <ChevronsUpDown v-else :size="12" />
                </button>
              </th>
              <th>Runs</th>
              <th>Created</th>
              <th>Account</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="job in sortedJobs" :key="job.job_id">
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
              <td class="mono">{{ job.last_fired_at ? relativeTime(job.last_fired_at) : "-" }}</td>
              <td>{{ job.run_count }}{{ job.max_runs ? `/${job.max_runs}` : "" }}</td>
              <td class="mono">{{ relativeTime(job.created_at) }}</td>
              <td class="mono account-key" :title="job.sender_account_key || '—'">{{ job.sender_account_key || '—' }}</td>
              <td>
                <div class="action-buttons">
                  <button class="action-btn" title="Edit" @click="openEdit(job)">
                    <Pencil :size="14" />
                  </button>
                  <button
                    v-if="job.is_active"
                    class="action-btn"
                    title="Cancel"
                    @click="confirmCancel(job)"
                  >
                    <Ban :size="14" />
                  </button>
                  <button
                    v-else
                    class="action-btn"
                    title="Activate"
                    @click="confirmActivate(job)"
                  >
                    <Play :size="14" />
                  </button>
                  <button class="action-btn action-btn-danger" title="Delete" @click="confirmDelete(job)">
                    <Trash2 :size="14" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>

    <!-- Mobile: card view -->
    <div v-if="data && data.jobs.length" class="mobile-cards mobile-only">
      <Card v-for="job in sortedJobs" :key="job.job_id" class="job-card">
        <div class="job-card-header">
          <span class="mono job-id">{{ job.job_id.slice(0, 8) }}</span>
          <Badge :variant="statusVariant(job)">{{ statusLabel(job) }}</Badge>
        </div>

        <div class="job-card-target">
          <Target :size="12" />
          <span>{{ job.platform }}</span>
          <span class="sub">{{ job.chat_type }}:{{ job.chat_id }}</span>
        </div>

        <div class="job-card-badges">
          <Badge variant="outline">{{ modeLabel(job.mode) }}</Badge>
          <Badge variant="secondary">{{ job.session_mode }}</Badge>
        </div>

        <div class="job-card-prompt">
          <MessageSquare :size="12" />
          <span>{{ job.prompt.slice(0, 80) }}{{ job.prompt.length > 80 ? "..." : "" }}</span>
        </div>

        <div class="job-card-meta">
          <div class="meta-item">
            <Clock :size="12" />
            <span class="mono">{{ job.next_fire_at ? relativeTime(job.next_fire_at) : "-" }}</span>
          </div>
          <div class="meta-item">
            <Clock :size="12" />
            <span class="mono">{{ job.last_fired_at ? relativeTime(job.last_fired_at) : "-" }}</span>
          </div>
          <div class="meta-item">
            <Hash :size="12" />
            <span>{{ job.run_count }}{{ job.max_runs ? `/${job.max_runs}` : "" }}</span>
          </div>
          <div v-if="job.sender_account_key" class="meta-item">
            <User :size="12" />
            <span class="mono account-key" :title="job.sender_account_key">{{ job.sender_account_key }}</span>
          </div>
        </div>

        <div class="job-card-actions">
          <button class="card-action-btn" @click="openEdit(job)">
            <Pencil :size="16" />
            <span>Edit</span>
          </button>
          <button
            v-if="job.is_active"
            class="card-action-btn"
            @click="confirmCancel(job)"
          >
            <Ban :size="16" />
            <span>Cancel</span>
          </button>
          <button
            v-else
            class="card-action-btn"
            @click="confirmActivate(job)"
          >
            <Play :size="16" />
            <span>Activate</span>
          </button>
          <button class="card-action-btn card-action-btn-danger" @click="confirmDelete(job)">
            <Trash2 :size="16" />
            <span>Delete</span>
          </button>
        </div>
      </Card>
    </div>

    <div v-if="data && !data.jobs.length" class="empty">
      No CRON jobs found.
    </div>

    <!-- Create/Edit dialog -->
    <CronJobFormDialog
      ref="formRef"
      v-model:open="showFormDialog"
      :loading="formLoading"
      :job="editingJob"
      @submit="handleFormSubmit"
    />

    <!-- Activate confirm -->
    <ConfirmDialog
      v-model:open="showActivateDialog"
      title="Activate CRON Job"
      :description="`Activate job ${activateJobLabel}? It will resume scheduling from the next valid fire time.`"
      confirm-label="Activate Job"
      :loading="activateMutation.isPending.value"
      @confirm="executeActivate"
    />

    <!-- Cancel confirm -->
    <ConfirmDialog
      v-model:open="showCancelDialog"
      title="Cancel CRON Job"
      :description="`Cancel job ${cancelJobLabel}? This will deactivate it.`"
      confirm-label="Cancel Job"
      :loading="cancelMutation.isPending.value"
      @confirm="executeCancel"
    />

    <!-- Delete confirm -->
    <ConfirmDialog
      v-model:open="showDeleteDialog"
      title="Delete CRON Job"
      :description="`Permanently delete job ${deleteJobLabel}? This cannot be undone.`"
      variant="destructive"
      confirm-label="Delete"
      :loading="deleteMutation.isPending.value"
      @confirm="executeDelete"
    />
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

/* ── Toolbar ── */

.cron-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  gap: 0.375rem;
  flex-wrap: wrap;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.job-count {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  white-space: nowrap;
}

/* ── Table (desktop) ── */

.desktop-only {
  display: block;
}

.mobile-only {
  display: none;
}

.cron-table-card {
  padding: 0;
  overflow: hidden;
}

.table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.cron-table {
  width: 100%;
  min-width: 960px;
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

.sort-button {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-transform: inherit;
  letter-spacing: inherit;
  cursor: pointer;
}

.sort-button:hover {
  color: var(--color-foreground);
}

.sort-button svg {
  flex-shrink: 0;
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

.action-buttons {
  display: flex;
  gap: 0.25rem;
}

.action-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-muted-foreground);
  padding: 0.25rem;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s, background 0.15s;
}

.action-btn:hover {
  color: var(--color-foreground);
  background: var(--color-accent);
}

.action-btn-danger:hover {
  color: var(--color-destructive);
}

.empty {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  text-align: center;
  padding: 2rem;
}

/* ── Mobile cards ── */

@media (max-width: 768px) {
  .desktop-only {
    display: none;
  }

  .mobile-only {
    display: block;
  }

  .cron-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-right {
    justify-content: space-between;
  }

  .mobile-cards {
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
  }

  .job-card {
    padding: 0.75rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .job-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .job-id {
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
  }

  .job-card-target {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.8125rem;
    color: var(--color-foreground);
  }

  .job-card-target .sub {
    color: var(--color-muted-foreground);
  }

  .job-card-badges {
    display: flex;
    gap: 0.375rem;
    flex-wrap: wrap;
  }

  .job-card-prompt {
    display: flex;
    align-items: flex-start;
    gap: 0.375rem;
    font-size: 0.8125rem;
    color: var(--color-muted-foreground);
    line-height: 1.4;
    padding: 0.5rem;
    background: var(--color-muted);
    border-radius: var(--radius-sm);
  }

  .job-card-prompt svg {
    flex-shrink: 0;
    margin-top: 0.15rem;
  }

  .job-card-meta {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .meta-item {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.75rem;
    color: var(--color-muted-foreground);
  }

  .job-card-actions {
    display: flex;
    gap: 0.375rem;
    padding-top: 0.375rem;
    border-top: 1px solid var(--color-border);
  }

  .card-action-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.25rem;
    padding: 0.5rem 0.375rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-background);
    color: var(--color-foreground);
    font-size: 0.75rem;
    cursor: pointer;
    transition: background 0.15s;
    -webkit-tap-highlight-color: transparent;
  }

  .card-action-btn:active {
    background: var(--color-accent);
  }

  .card-action-btn-danger:active {
    background: color-mix(in srgb, var(--color-destructive) 12%, transparent);
    color: var(--color-destructive);
  }

  .account-key {
    max-width: 14em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}
</style>
