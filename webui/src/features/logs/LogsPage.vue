<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue";
import { useLogs } from "@/api/queries";
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
  // Keep only HH:MM:SS.mmm
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

const scrollContainer = ref<HTMLElement | null>(null);

watch(
  () => data.value?.entries.length,
  async () => {
    if (paused.value) return;
    await nextTick();
    const el = scrollContainer.value;
    if (!el) return;
    // Only auto-scroll when near the bottom
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  },
);
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

    <div
      v-if="data"
      ref="scrollContainer"
      class="log-entries"
    >
      <div
        v-for="(entry, i) in data.entries"
        :key="i"
        class="log-entry"
        :class="entry.level"
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
      </div>
    </div>
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
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.25rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
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

</style>
