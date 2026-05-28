<script setup lang="ts">
import { computed, ref } from "vue";
import {
  useDeliveryGroups,
  useMessageDeliveries,
  useSessionHistory,
  useSessionList,
  useSessionSearch,
} from "@/api/queries";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import { formatDateTime, relativeTime } from "@/lib/utils";
import type { SessionSearchResult, SessionSummary } from "@/api/schemas";

type ViewMode = "history" | "search";
type ItemType = "session" | "delivery";

interface DisplayItem {
  id: string;
  label: string;
  type: ItemType;
  count: number;
  lastActiveAt: string;
  workspaceId: string;
  kind: string;
  source: string;
}

interface SessionGroup {
  key: string;
  items: DisplayItem[];
}

const { data: sessionData, isLoading, isFetching, error, refetch } = useSessionList();
const deliveryGroupsQuery = useDeliveryGroups();
const selectedId = ref("");
const activeView = ref<ViewMode>("history");

const selectedType = computed<ItemType>(() =>
  selectedId.value.startsWith("delivery:") ? "delivery" : "session",
);
const selectedSessionId = computed(() =>
  selectedType.value === "session" ? selectedId.value : "",
);
const selectedDeliveryTarget = computed(() =>
  selectedType.value === "delivery" ? selectedId.value.slice("delivery:".length) : "",
);

const historyQuery = useSessionHistory(selectedSessionId);
const deliveriesQuery = useMessageDeliveries(selectedDeliveryTarget);

const searchText = ref("");
const searchChatAddress = ref("");
const searchSource = ref("");
const searchRole = ref("");
const searchParams = computed(() => ({
  q: searchText.value.trim(),
  chat_address: searchChatAddress.value.trim(),
  source: searchSource.value.trim(),
  role: searchRole.value,
}));
const searchEnabled = computed(() => activeView.value === "search");
const searchQuery = useSessionSearch(searchParams, searchEnabled);

const groups = computed<SessionGroup[]>(() => {
  const map = new Map<string, DisplayItem[]>();
  const sessions = sessionData.value?.sessions ?? [];
  for (const session of sessions) {
    const key = chatGroupKey(session.session_id);
    const list = ensureGroup(map, key);
    list.push(sessionItem(session));
  }

  for (const group of deliveryGroupsQuery.data.value?.groups ?? []) {
    const key = group.target_chat_address;
    const list = ensureGroup(map, key);
    list.unshift({
      id: `delivery:${group.target_chat_address}`,
      label: "Message deliveries",
      type: "delivery",
      count: group.count,
      lastActiveAt: group.last_created_at,
      workspaceId: "",
      kind: "delivery",
      source: group.last_source,
    });
  }

  return Array.from(map.entries())
    .map(([key, items]) => ({
      key,
      items: items.sort(sortDisplayItems),
    }))
    .sort((a, b) => latestTime(b.items) - latestTime(a.items));
});

function ensureGroup(map: Map<string, DisplayItem[]>, key: string) {
  let list = map.get(key);
  if (!list) {
    list = [];
    map.set(key, list);
  }
  return list;
}

function sessionItem(session: SessionSummary): DisplayItem {
  return {
    id: session.session_id,
    label: session.session_id,
    type: "session",
    count: session.turn_count,
    lastActiveAt: session.last_active_at,
    workspaceId: session.workspace_id ?? "default",
    kind: session.session_key_kind,
    source: "",
  };
}

function sortDisplayItems(a: DisplayItem, b: DisplayItem) {
  if (a.type !== b.type) return a.type === "delivery" ? -1 : 1;
  return new Date(b.lastActiveAt).getTime() - new Date(a.lastActiveAt).getTime();
}

function latestTime(items: DisplayItem[]) {
  return Math.max(0, ...items.map((item) => new Date(item.lastActiveAt).getTime() || 0));
}

function chatGroupKey(sessionId: string): string {
  const parts = sessionId.split(":");
  const typed = ["private", "group", "channel", "thread", "unknown"];
  if (parts.length >= 3 && typed.includes(parts[1])) return `${parts[0]}:${parts[1]}:${parts[2]}`;
  if (parts.length >= 2) return `${parts[0]}:${parts[1]}`;
  return sessionId;
}

function selectItem(item: DisplayItem) {
  selectedId.value = item.id;
  activeView.value = "history";
  if (item.type === "delivery") searchChatAddress.value = item.id.slice("delivery:".length);
}

function selectSearchResult(result: SessionSearchResult) {
  if (result.result_type === "delivery") {
    selectedId.value = `delivery:${result.target_chat_address}`;
  } else {
    selectedId.value = result.session_id;
  }
  activeView.value = "history";
}

function roleVariant(role: string) {
  switch (role) {
    case "user": return "default";
    case "assistant": return "success";
    case "system": return "secondary";
    case "delivery": return "warning";
    default: return "outline";
  }
}

function sourceBadgeVariant(source: string) {
  if (source === "cron_trigger" || source === "scheduler_cron") return "warning";
  if (source === "cross_session_message" || source === "message_tool") return "secondary";
  if (source === "webapi_send") return "outline";
  return "outline";
}

function shortMeta(item: DisplayItem) {
  if (item.type === "delivery") return `${item.count} deliveries`;
  return `${item.count} turns`;
}

function resultTitle(result: SessionSearchResult) {
  return result.result_type === "delivery"
    ? result.target_chat_address
    : result.session_id;
}

function resultSource(result: SessionSearchResult) {
  const parts = [result.source, result.delivery_mode, result.status].filter(Boolean);
  return parts.join(" / ");
}
</script>

<template>
  <div class="sessions-page">
    <Alert v-if="error" variant="destructive">
      Failed to load sessions: {{ error.message }}
    </Alert>
    <Alert v-if="deliveryGroupsQuery.error.value" variant="destructive">
      Failed to load delivery groups: {{ deliveryGroupsQuery.error.value.message }}
    </Alert>

    <div v-if="isLoading" class="loading">Loading...</div>

    <div v-if="sessionData" class="sessions-layout">
      <aside class="session-list">
        <div class="session-list-header">
          <span class="session-list-title">Sessions</span>
          <button class="refresh-btn" :disabled="isFetching" @click="refetch()">
            {{ isFetching ? "Refreshing..." : "Refresh" }}
          </button>
        </div>
        <div v-if="!groups.length" class="empty">No sessions found.</div>
        <div v-for="group in groups" :key="group.key" class="session-group">
          <div class="group-header">{{ group.key }}</div>
          <button
            v-for="item in group.items"
            :key="item.id"
            type="button"
            class="session-item"
            :class="{ selected: selectedId === item.id, delivery: item.type === 'delivery' }"
            @click="selectItem(item)"
          >
            <div class="session-id">{{ item.label }}</div>
            <div class="session-meta">
              <span>{{ shortMeta(item) }}</span>
              <span>|</span>
              <span :title="formatDateTime(item.lastActiveAt)">{{ relativeTime(item.lastActiveAt) }}</span>
              <span v-if="item.workspaceId">|</span>
              <span v-if="item.workspaceId">{{ item.workspaceId }}</span>
            </div>
          </button>
        </div>
      </aside>

      <main class="history-viewer">
        <div class="view-tabs">
          <button :class="{ active: activeView === 'history' }" type="button" @click="activeView = 'history'">
            History
          </button>
          <button :class="{ active: activeView === 'search' }" type="button" @click="activeView = 'search'">
            Search
          </button>
        </div>

        <section v-if="activeView === 'history'" class="view-pane">
          <div v-if="!selectedId" class="empty">Select a session to view history.</div>

          <template v-else-if="selectedType === 'session'">
            <div v-if="historyQuery.isLoading.value" class="loading">Loading history...</div>
            <template v-else-if="historyQuery.data.value">
              <div class="history-header">
                <code>{{ selectedSessionId }}</code>
                <span class="turn-count">{{ historyQuery.data.value.turns.length }} turns</span>
              </div>
              <div class="history-turns">
                <article v-for="turn in historyQuery.data.value.turns" :key="turn.turn_id" class="turn">
                  <div class="turn-header">
                    <Badge :variant="roleVariant(turn.role)">{{ turn.role }}</Badge>
                    <Badge v-if="turn.source" :variant="sourceBadgeVariant(turn.source)">{{ turn.source }}</Badge>
                    <Badge v-if="turn.sentinel_action" variant="destructive">{{ turn.sentinel_action }}</Badge>
                    <span class="turn-time" :title="formatDateTime(turn.created_at)">
                      {{ relativeTime(turn.created_at) }}
                    </span>
                  </div>
                  <pre class="turn-content">{{ turn.content }}</pre>
                </article>
              </div>
            </template>
          </template>

          <template v-else>
            <div v-if="deliveriesQuery.isLoading.value" class="loading">Loading deliveries...</div>
            <template v-else-if="deliveriesQuery.data.value">
              <div class="history-header">
                <code>{{ selectedDeliveryTarget }}</code>
                <span class="turn-count">{{ deliveriesQuery.data.value.deliveries.length }} deliveries</span>
              </div>
              <div class="history-turns">
                <article
                  v-for="delivery in deliveriesQuery.data.value.deliveries"
                  :key="delivery.delivery_id"
                  class="turn"
                >
                  <div class="turn-header">
                    <Badge variant="warning">delivery</Badge>
                    <Badge v-if="delivery.source" :variant="sourceBadgeVariant(delivery.source)">{{ delivery.source }}</Badge>
                    <Badge v-if="delivery.delivery_mode" variant="outline">{{ delivery.delivery_mode }}</Badge>
                    <Badge v-if="delivery.sentinel_action" variant="destructive">{{ delivery.sentinel_action }}</Badge>
                    <span class="turn-time" :title="formatDateTime(delivery.created_at)">
                      {{ relativeTime(delivery.created_at) }}
                    </span>
                  </div>
                  <pre class="turn-content">{{ delivery.text }}</pre>
                  <div v-if="delivery.error" class="delivery-error">{{ delivery.error }}</div>
                </article>
              </div>
            </template>
          </template>
        </section>

        <section v-else class="view-pane">
          <div class="search-controls">
            <input v-model="searchText" type="search" placeholder="Search text" />
            <input v-model="searchChatAddress" type="search" placeholder="Chat address" />
            <input v-model="searchSource" type="search" placeholder="Source" />
            <select v-model="searchRole">
              <option value="">Any role</option>
              <option value="user">User</option>
              <option value="assistant">Assistant</option>
              <option value="system">System</option>
              <option value="delivery">Delivery</option>
            </select>
          </div>

          <div v-if="searchQuery.isLoading.value" class="loading">Searching...</div>
          <div v-else-if="searchQuery.data.value && !searchQuery.data.value.results.length" class="empty">
            No matching records.
          </div>
          <div v-else-if="searchQuery.data.value" class="history-turns">
            <article v-for="result in searchQuery.data.value.results" :key="`${result.result_type}:${result.id}`" class="turn">
              <div class="turn-header">
                <Badge :variant="roleVariant(result.role)">{{ result.role }}</Badge>
                <Badge v-if="result.source" :variant="sourceBadgeVariant(result.source)">{{ result.source }}</Badge>
                <Badge v-if="result.sentinel_action" variant="destructive">{{ result.sentinel_action }}</Badge>
                <span class="turn-time" :title="formatDateTime(result.created_at)">{{ relativeTime(result.created_at) }}</span>
                <button class="jump-btn" type="button" @click="selectSearchResult(result)">Open</button>
              </div>
              <div class="search-target">{{ resultTitle(result) }}</div>
              <div v-if="resultSource(result)" class="turn-source">{{ resultSource(result) }}</div>
              <pre class="turn-content">{{ result.content }}</pre>
            </article>
          </div>
        </section>
      </main>
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
  padding: 2rem;
  text-align: center;
}

.sessions-layout {
  display: grid;
  grid-template-columns: minmax(260px, 320px) 1fr;
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
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}

.session-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 0.25rem;
}

.session-list-title {
  font-size: 0.8125rem;
  font-weight: 600;
}

.refresh-btn,
.jump-btn,
.view-tabs button {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-muted);
  color: var(--color-foreground);
  cursor: pointer;
}

.refresh-btn {
  padding: 0.2rem 0.5rem;
  font-size: 0.6875rem;
}

.refresh-btn:hover:not(:disabled),
.jump-btn:hover,
.view-tabs button:hover {
  background: var(--color-accent);
}

.refresh-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.group-header {
  padding: 0.25rem 0;
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.session-item {
  display: block;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-foreground);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, border-color 0.15s;
}

.session-item:hover,
.session-item.selected {
  background: var(--color-accent);
}

.session-item.selected {
  border-color: var(--color-ring);
}

.session-item.delivery {
  border-color: color-mix(in srgb, var(--color-warning) 35%, transparent);
}

.session-id {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  overflow-wrap: anywhere;
}

.session-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-top: 0.125rem;
  color: var(--color-muted-foreground);
  font-size: 0.6875rem;
}

.history-viewer {
  min-width: 0;
  max-height: calc(100vh - 140px);
  overflow-y: auto;
}

.view-tabs {
  display: inline-flex;
  gap: 0.25rem;
  margin-bottom: 0.75rem;
}

.view-tabs button {
  padding: 0.3rem 0.75rem;
  font-size: 0.75rem;
}

.view-tabs button.active {
  border-color: var(--color-ring);
  background: var(--color-accent);
}

.history-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
  font-size: 0.8125rem;
}

.history-header code {
  padding: 0.125rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--color-muted);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}

.turn-count,
.turn-time,
.turn-source,
.search-target {
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
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
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 0.375rem;
}

.turn-time {
  margin-left: auto;
}

.turn-content {
  max-height: 260px;
  margin: 0;
  overflow-y: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
  font-size: 0.8125rem;
  line-height: 1.5;
}

.delivery-error {
  margin-top: 0.5rem;
  color: var(--color-destructive);
  font-size: 0.75rem;
}

.search-controls {
  display: grid;
  grid-template-columns: 2fr 1.5fr 1fr 120px;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}

@media (max-width: 960px) {
  .search-controls {
    grid-template-columns: 1fr;
  }
}

.search-controls input,
.search-controls select {
  min-width: 0;
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-background);
  color: var(--color-foreground);
  font-size: 0.8125rem;
}

.jump-btn {
  padding: 0.18rem 0.55rem;
  font-size: 0.6875rem;
}

.search-target {
  margin-bottom: 0.25rem;
  font-family: var(--font-mono);
  overflow-wrap: anywhere;
}
</style>
