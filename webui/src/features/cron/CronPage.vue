<script setup lang="ts">
import { ref, computed } from "vue";
import { useCronList, useCronCreate, useCronUpdate, useCronCancel, useCronDelete } from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import CronJobFormDialog from "./CronJobFormDialog.vue";
import type { CronJob } from "@/api/schemas";
import { relativeTime } from "@/lib/utils";
import { Pencil, Ban, Trash2, Plus } from "lucide-vue-next";

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

// Mutations
const createMutation = useCronCreate();
const updateMutation = useCronUpdate();
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
      { onSettled: () => { showFormDialog.value = false; } },
    );
  } else {
    const payload = formRef.value.buildCreatePayload();
    createMutation.mutate(payload, {
      onSettled: () => { showFormDialog.value = false; },
    });
  }
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
            <th>Actions</th>
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
                <button class="action-btn action-btn-danger" title="Delete" @click="confirmDelete(job)">
                  <Trash2 :size="14" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </Card>

    <div v-if="data && !data.jobs.length" class="empty">
      No CRON jobs found.
    </div>

    <!-- Create/Edit dialog -->
    <CronJobFormDialog
      ref="formRef"
      v-model:open="showFormDialog"
      v-model:loading="formLoading"
      :job="editingJob"
      @submit="handleFormSubmit"
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

.cron-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.filter-group {
  display: flex;
  gap: 0.375rem;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
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
</style>
