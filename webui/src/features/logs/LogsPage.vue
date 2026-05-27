<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue";
import {
  DialogRoot,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogTrigger,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "reka-ui";
import { useLogs } from "@/api/queries";
import type { LogEntry } from "@/api/schemas";
import Card from "@/components/ui/Card.vue";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";

const levelOptions = ["ALL", "ERROR", "WARNING", "INFO", "DEBUG", "TRACE"];
const levelFilter = ref("ALL");
const loggerFilter = ref("");
const searchFilter = ref("");
const paused = ref(false);

const params = computed(() => ({
  level: levelFilter.value,
  logger: loggerFilter.value,
  search: searchFilter.value,
}));

const { data, isLoading } = useLogs(params, paused);

function levelVariant(level: string) {
  switch (level) {
    case "error":
    case "critical":
      return "destructive";
    case "warning":
      return "warning";
    case "info":
      return "outline";
    case "debug":
      return "secondary";
    default:
      return "outline";
  }
}

function formatTime(ts: string) {
  if (!ts) return "";
  const idx = ts.indexOf("T");
  if (idx === -1) return ts;
  return ts.slice(idx + 1, idx + 12);
}

function formatFields(fields: Record<string, unknown>) {
  const entries = Object.entries(fields);
  if (!entries.length) return "";
  return entries
    .filter(([k]) => !k.startsWith("_"))
    .map(([k, v]) => {
      const s = typeof v === "string" ? v : JSON.stringify(v);
      const truncated = s && s.length > 80 ? s.slice(0, 80) + "..." : s;
      return `${k}=${truncated}`;
    })
    .join("  ");
}

function formatFieldValue(v: unknown) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

const scrollContainer = ref<HTMLElement | null>(null);

watch(
  () => data.value?.entries.length,
  async () => {
    if (paused.value) return;
    await nextTick();
    const el = scrollContainer.value;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  },
);

const detailOpen = ref(false);
const selected = ref<LogEntry | null>(null);

function selectDetail(entry: LogEntry) {
  selected.value = entry;
}
</script>

<template>
  <div class="logs-page">
    <div class="logs-toolbar">
      <div class="level-buttons">
        <Button
          v-for="lvl in levelOptions"
          :key="lvl"
          :variant="levelFilter === lvl ? 'default' : 'outline'"
          size="sm"
          @click="levelFilter = lvl"
        >
          {{ lvl }}
        </Button>
      </div>
      <input
        v-model="loggerFilter"
        class="filter-input"
        type="text"
        placeholder="Filter logger..."
      />
      <input
        v-model="searchFilter"
        class="filter-input"
        type="text"
        placeholder="Search..."
      />
      <Button
        :variant="paused ? 'default' : 'outline'"
        size="sm"
        @click="paused = !paused"
      >
        {{ paused ? "Resume" : "Pause" }}
      </Button>
      <span class="entry-count">
        {{ data?.entries.length ?? 0 }} entries
      </span>
    </div>

    <Card v-if="isLoading && !data" class="loading">Loading...</Card>

    <DialogRoot v-model:open="detailOpen">
      <div
        v-if="data"
        ref="scrollContainer"
        class="log-entries"
      >
        <DialogTrigger
          v-for="(entry, i) in data.entries"
          :key="i"
          as-child
        >
          <button
            type="button"
            class="log-entry"
            :class="entry.level"
            @click="selectDetail(entry)"
          >
            <span class="log-time">{{ formatTime(entry.timestamp) }}</span>
            <Badge :variant="levelVariant(entry.level)" size="sm">
              {{ entry.level }}
            </Badge>
            <span class="log-logger" :title="entry.logger">{{ entry.logger }}</span>
            <span class="log-event">{{ entry.event }}</span>
            <span v-if="Object.keys(entry.fields).length" class="log-fields">
              {{ formatFields(entry.fields) }}
            </span>
          </button>
        </DialogTrigger>
      </div>

      <DialogPortal>
        <DialogOverlay class="dialog-overlay" />
        <DialogContent class="dialog-content">
          <template v-if="selected">
            <div class="detail-header">
              <div class="detail-title">
                <Badge :variant="levelVariant(selected.level)" size="sm">
                  {{ selected.level }}
                </Badge>
                <DialogTitle class="detail-event">{{ selected.event }}</DialogTitle>
                <DialogDescription class="sr-only">
                  Full details for the selected log entry.
                </DialogDescription>
              </div>
              <DialogClose as-child>
                <button class="detail-close" aria-label="Close">&times;</button>
              </DialogClose>
            </div>
            <div class="detail-body">
              <div class="detail-row">
                <span class="detail-label">Time</span>
                <code>{{ selected.timestamp }}</code>
              </div>
              <div class="detail-row">
                <span class="detail-label">Logger</span>
                <code>{{ selected.logger }}</code>
              </div>
              <div v-if="Object.keys(selected.fields).length" class="detail-fields">
                <div
                  v-for="(val, key) in selected.fields"
                  :key="key"
                  class="detail-field"
                >
                  <span class="detail-label">{{ key }}</span>
                  <pre class="detail-value">{{ formatFieldValue(val) }}</pre>
                </div>
              </div>
              <div v-else class="detail-empty">No additional fields.</div>
            </div>
          </template>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </div>
</template>

<style scoped>
.logs-page {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  height: 100%;
}

.logs-toolbar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.level-buttons {
  display: flex;
  gap: 0.25rem;
}

.filter-input {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 0.3125rem 0.625rem;
  font-size: 0.75rem;
  color: var(--color-foreground);
  width: 140px;
}

.filter-input::placeholder {
  color: var(--color-muted-foreground);
}

.entry-count {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  margin-left: auto;
}

.loading {
  padding: 0.75rem 1rem;
  font-size: 0.8125rem;
  color: var(--color-muted-foreground);
}

.log-entries {
  flex: 1;
  overflow-y: auto;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  line-height: 1.5;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.log-entry {
  appearance: none;
  width: 100%;
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.25rem 0.75rem;
  border: 0;
  border-bottom: 1px solid var(--color-border);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.1s;
}

.log-entry:hover,
.log-entry:focus-visible {
  background: var(--color-accent);
}

.log-entry:focus-visible {
  outline: 2px solid var(--color-ring);
  outline-offset: -2px;
}

.log-entry:last-child {
  border-bottom: none;
}

.log-time {
  color: var(--color-muted-foreground);
  white-space: nowrap;
  flex-shrink: 0;
}

.log-logger {
  color: var(--color-muted-foreground);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}

.log-event {
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}

.log-fields {
  color: var(--color-muted-foreground);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Dialog */
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: oklch(0 0 0 / 0.4);
  animation: overlay-in 0.15s ease-out;
}

@keyframes overlay-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.dialog-content {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 51;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  width: min(560px, 90vw);
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px oklch(0 0 0 / 0.2);
  animation: content-in 0.15s ease-out;
}

@keyframes content-in {
  from { opacity: 0; transform: translate(-50%, -48%); }
  to { opacity: 1; transform: translate(-50%, -50%); }
}

.dialog-content:focus {
  outline: none;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.detail-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.detail-event {
  min-width: 0;
  font-size: 0.875rem;
  font-weight: 600;
  font-family: var(--font-mono);
  overflow-wrap: anywhere;
}

.detail-close {
  flex-shrink: 0;
  background: none;
  border: none;
  font-size: 1.25rem;
  cursor: pointer;
  color: var(--color-muted-foreground);
  padding: 0 0.25rem;
  line-height: 1;
}

.detail-close:hover {
  color: var(--color-foreground);
}

.detail-body {
  padding: 0.75rem 1rem;
  overflow-y: auto;
  font-size: 0.8125rem;
  font-family: var(--font-mono);
}

.detail-row {
  display: flex;
  gap: 0.75rem;
  padding: 0.375rem 0;
  min-width: 0;
  align-items: flex-start;
}

.detail-row code {
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.detail-label {
  color: var(--color-muted-foreground);
  min-width: 80px;
  flex-shrink: 0;
}

.detail-fields {
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.detail-field {
  border-top: 1px solid var(--color-border);
  padding-top: 0.5rem;
}

.detail-value {
  margin: 0.25rem 0 0;
  font-size: 0.75rem;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--color-muted);
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
}

.detail-empty {
  color: var(--color-muted-foreground);
  font-style: italic;
  padding-top: 0.5rem;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
