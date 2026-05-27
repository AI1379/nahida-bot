<script setup lang="ts">
import { ref } from "vue";
import { useStatus, useSystemRestart, useSystemShutdown } from "@/api/queries";
import Card from "@/components/ui/Card.vue";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Textarea from "@/components/ui/Textarea.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import { formatBytes, formatDuration, formatNumber } from "@/lib/utils";

const { data: status, isLoading, error } = useStatus();

const showRestartDialog = ref(false);
const showShutdownDialog = ref(false);
const actionReason = ref("");
const actionRequested = ref(false);

const restartMutation = useSystemRestart();
const shutdownMutation = useSystemShutdown();

function handleRestart() {
  restartMutation.mutate(actionReason.value, {
    onSettled: () => {
      actionRequested.value = true;
      showRestartDialog.value = false;
      actionReason.value = "";
    },
  });
}

function handleShutdown() {
  shutdownMutation.mutate(actionReason.value, {
    onSettled: () => {
      actionRequested.value = true;
      showShutdownDialog.value = false;
      actionReason.value = "";
    },
  });
}
</script>

<template>
  <div class="home-page">
    <Alert v-if="error" variant="destructive">
      Failed to load status: {{ error.message }}
    </Alert>

    <div v-if="isLoading" class="loading">Loading...</div>

    <template v-if="status">
      <!-- App info -->
      <section class="section">
        <h2 class="section-title">Application</h2>
        <div class="grid grid-2">
          <Card>
            <div class="metric-label">App</div>
            <div class="metric-value">{{ status.app.name }}</div>
            <div class="metric-sub">v{{ status.app.version }}</div>
          </Card>
          <Card>
            <div class="metric-label">Uptime</div>
            <div class="metric-value">
              {{ formatDuration(status.app.uptime_seconds) }}
            </div>
            <div class="metric-sub">
              PID {{ status.app.pid }} &middot;
              {{ status.app.debug ? "Debug" : "Production" }}
            </div>
          </Card>
        </div>
      </section>

      <!-- Services -->
      <section class="section">
        <h2 class="section-title">Services</h2>
        <Card>
          <div class="service-grid">
            <div
              v-for="(state, name) in status.services"
              :key="name"
              class="service-item"
            >
              <Badge
                :variant="
                  state === 'running'
                    ? 'success'
                    : state === 'degraded'
                      ? 'warning'
                      : 'destructive'
                "
              >
                {{ state }}
              </Badge>
              <span class="service-name">{{ name }}</span>
            </div>
          </div>
        </Card>
      </section>

      <!-- Resources -->
      <section class="section">
        <h2 class="section-title">Resources</h2>
        <div class="grid grid-4">
          <Card>
            <div class="metric-label">CPU</div>
            <div class="metric-value">{{ status.resources.cpu_percent.toFixed(1) }}%</div>
          </Card>
          <Card>
            <div class="metric-label">Memory (RSS)</div>
            <div class="metric-value">{{ formatBytes(status.resources.memory_rss_bytes) }}</div>
            <div class="metric-sub">{{ status.resources.memory_percent.toFixed(1) }}%</div>
          </Card>
          <Card>
            <div class="metric-label">Disk Free</div>
            <div class="metric-value">{{ formatBytes(status.resources.disk_free_bytes) }}</div>
          </Card>
          <Card>
            <div class="metric-label">DB Size</div>
            <div class="metric-value">{{ formatBytes(status.resources.db_size_bytes) }}</div>
          </Card>
        </div>
      </section>

      <!-- Token usage -->
      <section class="section">
        <h2 class="section-title">Token Usage</h2>
        <div class="grid grid-4">
          <Card>
            <div class="metric-label">Input</div>
            <div class="metric-value">{{ formatNumber(status.usage.input_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Output</div>
            <div class="metric-value">{{ formatNumber(status.usage.output_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Cached</div>
            <div class="metric-value">{{ formatNumber(status.usage.cached_tokens) }}</div>
          </Card>
          <Card>
            <div class="metric-label">Reasoning</div>
            <div class="metric-value">{{ formatNumber(status.usage.reasoning_tokens) }}</div>
          </Card>
        </div>
      </section>

      <!-- System Actions -->
      <section class="section">
        <h2 class="section-title">System Actions</h2>
        <Card>
          <div class="actions-row">
            <div class="actions-info">
              <span>Restart or shut down the server. An external supervisor must restart the process.</span>
            </div>
            <div class="actions-buttons">
              <Button
                variant="destructive"
                size="sm"
                :disabled="actionRequested"
                @click="showRestartDialog = true"
              >
                Restart
              </Button>
              <Button
                variant="destructive"
                size="sm"
                :disabled="actionRequested"
                @click="showShutdownDialog = true"
              >
                Shutdown
              </Button>
            </div>
          </div>
        </Card>
      </section>
    </template>

    <ConfirmDialog
      v-model:open="showRestartDialog"
      title="Restart Server"
      description="This will shut down the server. Your supervisor must restart the process."
      variant="destructive"
      confirm-label="Restart"
      :loading="restartMutation.isPending.value"
      @confirm="handleRestart"
    >
      <Textarea
        v-model="actionReason"
        placeholder="Reason (optional)"
        :rows="2"
        class="reason-input"
      />
    </ConfirmDialog>

    <ConfirmDialog
      v-model:open="showShutdownDialog"
      title="Shut Down Server"
      description="This will shut down the server. The process will exit."
      variant="destructive"
      confirm-label="Shut Down"
      :loading="shutdownMutation.isPending.value"
      @confirm="handleShutdown"
    >
      <Textarea
        v-model="actionReason"
        placeholder="Reason (optional)"
        :rows="2"
        class="reason-input"
      />
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.home-page {
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

.grid {
  display: grid;
  gap: 0.75rem;
}

.grid-2 {
  grid-template-columns: repeat(2, 1fr);
}

.grid-4 {
  grid-template-columns: repeat(4, 1fr);
}

@media (max-width: 900px) {
  .grid-4 {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 600px) {
  .grid-2,
  .grid-4 {
    grid-template-columns: 1fr;
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
  font-size: 1.25rem;
  font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.metric-sub {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  margin-top: 0.125rem;
}

.service-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.service-item {
  display: flex;
  align-items: center;
  gap: 0.375rem;
}

.service-name {
  font-size: 0.8125rem;
  font-weight: 500;
}

.actions-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.actions-info {
  font-size: 0.8125rem;
  color: var(--color-muted-foreground);
}

.actions-buttons {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

.reason-input {
  margin-top: 0.75rem;
}
</style>
