<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Play, RefreshCw, RotateCw, Square, Terminal } from "lucide-vue-next";
import { useProcessAction, useProcessList, useProcessLogs } from "@/api/queries";
import type {
  ProcessAction,
  ProcessHealth,
  ProcessInfo,
  ProcessStatus,
} from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";
import Spinner from "@/components/ui/Spinner.vue";

type BadgeVariant =
  | "default"
  | "success"
  | "warning"
  | "destructive"
  | "secondary"
  | "outline";

const statusMeta: Record<ProcessStatus, { label: string; variant: BadgeVariant }> =
  {
    pending: { label: "Pending", variant: "outline" },
    starting: { label: "Starting", variant: "default" },
    running: { label: "Running", variant: "success" },
    unhealthy: { label: "Unhealthy", variant: "warning" },
    stopping: { label: "Stopping", variant: "default" },
    stopped: { label: "Stopped", variant: "secondary" },
    failed: { label: "Failed", variant: "destructive" },
    disabled: { label: "Disabled", variant: "secondary" },
  };

const healthMeta: Record<ProcessHealth, { label: string; variant: BadgeVariant }> =
  {
    unknown: { label: "Unknown", variant: "outline" },
    healthy: { label: "Healthy", variant: "success" },
    unhealthy: { label: "Unhealthy", variant: "destructive" },
  };

const {
  data: processData,
  isLoading,
  isFetching,
  error,
  refetch,
} = useProcessList();

const processes = computed(() => processData.value?.processes ?? []);

const selectedName = ref("");
const logsOpen = ref(false);

watch(
  processes,
  (items) => {
    if (!items.length) return;
    if (!items.some((p) => p.name === selectedName.value)) {
      selectedName.value = items[0].name;
    }
  },
  { immediate: true },
);

const selectedProcess = computed(() =>
  processes.value.find((p) => p.name === selectedName.value),
);

const logsVisible = computed(() => logsOpen.value && selectedName.value !== "");
const { data: logsData } = useProcessLogs(selectedName, logsVisible);

const actionMutation = useProcessAction();

function runAction(process: ProcessInfo, action: ProcessAction) {
  actionMutation.mutate({ name: process.name, action });
}

function isPending(process: ProcessInfo, action: ProcessAction): boolean {
  const vars = actionMutation.variables.value;
  return (
    actionMutation.isPending.value
    && vars?.name === process.name
    && vars?.action === action
  );
}

function canStart(p: ProcessInfo): boolean {
  return ["stopped", "failed"].includes(p.status);
}

function canStop(p: ProcessInfo): boolean {
  return ["running", "starting", "unhealthy", "pending"].includes(p.status);
}

function startedAt(p: ProcessInfo): string {
  return p.started_at ? new Date(p.started_at).toLocaleString() : "-";
}
</script>

<template>
  <div class="processes-page">
    <Alert v-if="error" variant="destructive">
      Failed to load processes: {{ error.message }}
    </Alert>

    <Alert v-if="!isLoading && !error && !processes.length" variant="outline">
      No processes declared. Add a <code>processes:</code> section in
      config.yaml to supervise sidecars (SSH tunnels, frpc, cloudflared).
    </Alert>

    <section class="toolbar">
      <h2>Processes</h2>
      <Button variant="outline" size="sm" :disabled="isFetching" @click="refetch()">
        <RefreshCw :size="14" />
        Refresh
      </Button>
    </section>

    <div v-if="isLoading && !processData" class="loading">
      <Spinner /> Loading...
    </div>

    <Card v-else class="process-card">
      <div class="table-wrap">
        <table class="process-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Status</th>
              <th>Health</th>
              <th>PID</th>
              <th>Restarts</th>
              <th>Policy</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="p in processes"
              :key="p.name"
              :class="{ selected: selectedName === p.name }"
              @click="selectedName = p.name"
            >
              <td>
                <div class="name-cell">
                  <span class="proc-name">{{ p.name }}</span>
                  <code class="proc-owner">{{ p.owner }}</code>
                </div>
              </td>
              <td>
                <Badge :variant="statusMeta[p.status].variant">
                  {{ statusMeta[p.status].label }}
                </Badge>
              </td>
              <td>
                <Badge
                  v-if="p.health !== 'unknown'"
                  :variant="healthMeta[p.health].variant"
                >
                  {{ healthMeta[p.health].label }}
                </Badge>
                <span v-else class="muted">-</span>
              </td>
              <td class="mono">{{ p.pid ?? "-" }}</td>
              <td>{{ p.restart_count }}</td>
              <td class="mono">{{ p.restart_policy }}</td>
              <td @click.stop>
                <div class="actions">
                  <Button
                    v-if="canStart(p)"
                    size="sm"
                    variant="outline"
                    :disabled="isPending(p, 'start')"
                    @click="runAction(p, 'start')"
                  >
                    <Play :size="13" />
                    {{ isPending(p, "start") ? "Starting" : "Start" }}
                  </Button>
                  <Button
                    v-if="canStop(p)"
                    size="sm"
                    variant="outline"
                    :disabled="isPending(p, 'stop')"
                    @click="runAction(p, 'stop')"
                  >
                    <Square :size="13" />
                    {{ isPending(p, "stop") ? "Stopping" : "Stop" }}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    :disabled="isPending(p, 'restart')"
                    @click="runAction(p, 'restart')"
                  >
                    <RotateCw :size="13" />
                    {{ isPending(p, "restart") ? "Restarting" : "Restart" }}
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>

    <Card v-if="selectedProcess" class="detail-card">
      <header class="detail-header">
        <div>
          <h3>{{ selectedProcess.name }}</h3>
          <code class="proc-cmd">{{ selectedProcess.command }}</code>
        </div>
        <Button
          size="sm"
          variant="outline"
          :class="{ active: logsOpen }"
          @click="logsOpen = !logsOpen"
        >
          <Terminal :size="14" />
          Logs
        </Button>
      </header>

      <Alert v-if="selectedProcess.last_error" variant="destructive">
        {{ selectedProcess.last_error }}
      </Alert>

      <dl class="kv-list">
        <div>
          <dt>Started at</dt>
          <dd>{{ startedAt(selectedProcess) }}</dd>
        </div>
        <div>
          <dt>Exit code</dt>
          <dd>{{ selectedProcess.exit_code ?? "-" }}</dd>
        </div>
        <div>
          <dt>Owner</dt>
          <dd>{{ selectedProcess.owner }}</dd>
        </div>
        <div>
          <dt>Restart policy</dt>
          <dd class="mono">{{ selectedProcess.restart_policy }}</dd>
        </div>
      </dl>

      <section v-if="logsOpen" class="logs">
        <h4>stdout</h4>
        <pre class="log-box">{{ logsData?.stdout?.join("\n") || "(empty)" }}</pre>
        <h4>stderr</h4>
        <pre class="log-box err">{{ logsData?.stderr?.join("\n") || "(empty)" }}</pre>
      </section>
    </Card>
  </div>
</template>

<style scoped>
.processes-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.toolbar h2 {
  margin: 0;
  font-size: 1rem;
}

.loading {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.muted {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.mono {
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.table-wrap {
  overflow-x: auto;
}

.process-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.process-table th {
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-align: left;
  text-transform: uppercase;
  padding: 0.625rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.process-table td {
  padding: 0.625rem 0.75rem;
  border-bottom: 1px solid var(--color-border-subtle);
  vertical-align: middle;
}

.process-table tr:hover,
.process-table tr.selected {
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-card));
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  min-width: 0;
}

.proc-name {
  font-weight: 600;
}

.proc-owner {
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
  width: fit-content;
}

.actions {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.detail-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.detail-header h3 {
  margin: 0;
  font-size: 1rem;
}

.proc-cmd {
  display: block;
  margin-top: 0.375rem;
  max-width: 100%;
  overflow-wrap: anywhere;
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

.kv-list dt {
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

.kv-list dd {
  min-width: 0;
  margin: 0;
  font-size: 0.8125rem;
  overflow-wrap: anywhere;
}

.logs {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.logs h4 {
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  margin: 0.5rem 0 0;
  text-transform: uppercase;
}

.log-box {
  max-height: 16rem;
  overflow: auto;
  margin: 0;
  padding: 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-muted);
  font-family: var(--font-mono);
  font-size: 0.72rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-box.err {
  color: color-mix(in srgb, var(--color-foreground) 85%, #dc2626);
}

code {
  background: var(--color-muted);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 0.72rem;
  padding: 0.0625rem 0.3125rem;
}

@media (max-width: 720px) {
  .kv-list {
    grid-template-columns: 1fr;
  }

  .kv-list > div {
    grid-template-columns: 1fr;
    gap: 0.25rem;
  }
}
</style>
