<script setup lang="ts">
import { ref, computed } from "vue";
import { useSessionList, useSessionHistory } from "@/api/queries";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import type { SessionSummary } from "@/api/schemas";

const { data: sessionData, isLoading, error } = useSessionList();
const selectedId = ref("");

const historyQuery = useSessionHistory(selectedId);

interface SessionGroup {
  key: string;
  sessions: SessionSummary[];
}

const groups = computed<SessionGroup[]>(() => {
  if (!sessionData.value) return [];
  const map = new Map<string, typeof sessionData.value.sessions>();
  for (const s of sessionData.value.sessions) {
    const parts = s.session_id.split(":");
    const groupKey = parts.length >= 2 ? `${parts[0]}:${parts[1]}` : s.session_id;
    let list = map.get(groupKey);
    if (!list) {
      list = [];
      map.set(groupKey, list);
    }
    list.push(s);
  }
  return Array.from(map.entries()).map(([key, sessions]) => ({ key, sessions }));
});

function selectSession(id: string) {
  selectedId.value = id;
}

function roleVariant(role: string) {
  switch (role) {
    case "user": return "default";
    case "assistant": return "success";
    case "system": return "secondary";
    default: return "outline";
  }
}
</script>

<template>
  <div class="sessions-page">
    <Alert v-if="error" variant="destructive">
      Failed to load sessions: {{ error.message }}
    </Alert>

    <div v-if="isLoading" class="loading">Loading...</div>

    <div v-if="sessionData" class="sessions-layout">
      <!-- Session list -->
      <div class="session-list">
        <div v-if="!groups.length" class="empty">No sessions found.</div>
        <div
          v-for="group in groups"
          :key="group.key"
          class="session-group"
        >
          <div class="group-header">{{ group.key }}</div>
          <div
            v-for="s in group.sessions"
            :key="s.session_id"
            class="session-item"
            :class="{ selected: selectedId === s.session_id }"
            @click="selectSession(s.session_id)"
          >
            <div class="session-id">{{ s.session_id }}</div>
            <div class="session-meta">
              <span>{{ s.turn_count }} turns</span>
              <span>&middot;</span>
              <span>{{ s.workspace_id ?? "default" }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- History viewer -->
      <div class="history-viewer">
        <div v-if="!selectedId" class="empty">Select a session to view history.</div>
        <div v-else-if="historyQuery.isLoading.value" class="loading">Loading history...</div>
        <template v-else-if="historyQuery.data.value">
          <div class="history-header">
            <code>{{ selectedId }}</code>
            <span class="turn-count">{{ historyQuery.data.value.turns.length }} turns</span>
          </div>
          <div class="history-turns">
            <div
              v-for="turn in historyQuery.data.value.turns"
              :key="turn.turn_id"
              class="turn"
            >
              <div class="turn-header">
                <Badge :variant="roleVariant(turn.role)">{{ turn.role }}</Badge>
                <span v-if="turn.source" class="turn-source">{{ turn.source }}</span>
              </div>
              <pre class="turn-content">{{ turn.content }}</pre>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sessions-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.empty {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  text-align: center;
  padding: 2rem;
}

.sessions-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 1rem;
  min-height: 0;
}

@media (max-width: 768px) {
  .sessions-layout {
    grid-template-columns: 1fr;
  }
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
  max-height: calc(100vh - 140px);
}

.group-header {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-muted-foreground);
  padding: 0.25rem 0;
}

.session-item {
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-md);
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}

.session-item:hover {
  background: var(--color-accent);
}

.session-item.selected {
  background: var(--color-accent);
  border-color: var(--color-ring);
}

.session-id {
  font-size: 0.8125rem;
  font-family: var(--font-mono);
  word-break: break-all;
}

.session-meta {
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
  display: flex;
  gap: 0.375rem;
  margin-top: 0.125rem;
}

.history-viewer {
  overflow-y: auto;
  max-height: calc(100vh - 140px);
}

.history-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.8125rem;
}

.history-header code {
  font-size: 0.75rem;
  background: var(--color-muted);
  padding: 0.125rem 0.5rem;
  border-radius: var(--radius-sm);
}

.turn-count {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.history-turns {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.turn {
  padding: 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.turn-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.375rem;
}

.turn-source {
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
}

.turn-content {
  font-size: 0.8125rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-family: inherit;
  max-height: 200px;
  overflow-y: auto;
}
</style>
