<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  Archive,
  Database,
  FileClock,
  Plus,
  RefreshCw,
  Search,
} from "lucide-vue-next";
import {
  useMemoryArchive,
  useMemoryCandidates,
  useMemoryCreate,
  useMemoryItems,
  useMemoryProject,
  useMemoryTurns,
} from "@/api/queries";
import type {
  MemoryCreateRequest,
  MemoryItem,
  MemorySensitivity,
} from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import Input from "@/components/ui/Input.vue";
import Spinner from "@/components/ui/Spinner.vue";
import Textarea from "@/components/ui/Textarea.vue";
import { formatDateTime, relativeTime } from "@/lib/utils";

type MemoryTab = "items" | "candidates" | "turns";

const activeTab = ref<MemoryTab>("items");
const scopeType = ref("");
const scopeId = ref("");
const itemSearch = ref("");
const candidateStatus = ref("");

const turnSearch = ref("");
const turnChatAddress = ref("");
const turnSource = ref("");
const turnRole = ref("");

const itemParams = computed(() => ({
  q: itemSearch.value.trim(),
  scope_type: scopeType.value.trim(),
  scope_id: scopeId.value.trim(),
  limit: 150,
}));

const candidateParams = computed(() => ({
  status: candidateStatus.value,
  scope_type: scopeType.value.trim(),
  scope_id: scopeId.value.trim(),
  limit: 100,
}));

const turnParams = computed(() => ({
  q: turnSearch.value.trim(),
  chat_address: turnChatAddress.value.trim(),
  source: turnSource.value.trim(),
  role: turnRole.value,
  limit: 150,
}));

const {
  data: itemData,
  isLoading: itemsLoading,
  isFetching: itemsFetching,
  error: itemsError,
  refetch: refetchItems,
} = useMemoryItems(itemParams);
const candidatesQuery = useMemoryCandidates(candidateParams);
const turnsQuery = useMemoryTurns(
  turnParams,
  computed(() => activeTab.value === "turns"),
);

const createMut = useMemoryCreate();
const archiveMut = useMemoryArchive();
const projectMut = useMemoryProject();

const items = computed(() => itemData.value?.items ?? []);
const selectedItemId = ref("");
const selectedItem = computed(() =>
  items.value.find((item) => item.item_id === selectedItemId.value),
);

watch(
  items,
  (next) => {
    if (selectedItemId.value && next.some((item) => item.item_id === selectedItemId.value)) {
      return;
    }
    selectedItemId.value = next[0]?.item_id ?? "";
  },
  { immediate: true },
);

const restrictedCount = computed(
  () => items.value.filter((item) => item.sensitivity !== "public").length,
);
const totalImportance = computed(() =>
  items.value.reduce((total, item) => total + item.importance, 0),
);
const averageImportance = computed(() =>
  items.value.length ? totalImportance.value / items.value.length : 0,
);

const showCreateDialog = ref(false);
const newTitle = ref("");
const newContent = ref("");
const newKind = ref("fact");
const newSensitivity = ref<MemorySensitivity>("public");
const newConfidence = ref(1);
const newImportance = ref(0.5);
const newProject = ref(false);

const showArchiveDialog = ref(false);
const archiveTarget = ref<MemoryItem | null>(null);

const hasExactScope = computed(
  () => !!scopeType.value.trim() && !!scopeId.value.trim(),
);
const createDisabled = computed(
  () => !newContent.value.trim() || !hasExactScope.value,
);

function openCreate() {
  newTitle.value = "";
  newContent.value = "";
  newKind.value = "fact";
  newSensitivity.value = "public";
  newConfidence.value = 1;
  newImportance.value = 0.5;
  newProject.value = false;
  showCreateDialog.value = true;
}

async function doCreate() {
  if (createDisabled.value) return;
  const body = {
    title: newTitle.value.trim(),
    content: newContent.value.trim(),
    kind: newKind.value,
    scope_type: scopeType.value.trim(),
    scope_id: scopeId.value.trim(),
    sensitivity: newSensitivity.value,
    confidence: newConfidence.value,
    importance: newImportance.value,
    source: "webui",
    metadata: {},
    evidence: {},
    project_workspace: newProject.value,
  } satisfies MemoryCreateRequest;
  try {
    const result = await createMut.mutateAsync(body);
    selectedItemId.value = result.item_id;
    showCreateDialog.value = false;
  } catch {
    // Mutation toast keeps the dialog open.
  }
}

function openArchive(item: MemoryItem) {
  archiveTarget.value = item;
  showArchiveDialog.value = true;
}

async function doArchive() {
  if (!archiveTarget.value) return;
  try {
    await archiveMut.mutateAsync(archiveTarget.value.item_id);
    showArchiveDialog.value = false;
    archiveTarget.value = null;
  } catch {
    // Mutation toast keeps the dialog open.
  }
}

function projectCurrentScope() {
  if (!hasExactScope.value) return;
  projectMut.mutate({
    scope_type: scopeType.value.trim(),
    scope_id: scopeId.value.trim(),
  });
}

function sensitivityVariant(sensitivity: string) {
  if (sensitivity === "secret_like") return "destructive";
  if (sensitivity === "private") return "warning";
  return "success";
}

function roleVariant(role: string) {
  switch (role) {
    case "user":
      return "default";
    case "assistant":
      return "success";
    case "system":
      return "secondary";
    default:
      return "outline";
  }
}

function candidateVariant(status: string) {
  if (status === "auto_applied" || status === "applied") return "success";
  if (status === "rejected") return "destructive";
  if (status === "pending") return "warning";
  return "outline";
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function itemTitle(item: MemoryItem) {
  return item.title || item.kind || item.item_id;
}

function itemTime(item: MemoryItem) {
  return item.updated_at || item.created_at;
}

function scopeLabel(item: Pick<MemoryItem, "scope_type" | "scope_id">) {
  return `${item.scope_type}:${item.scope_id}`;
}

function truncate(text: string, max = 220) {
  const value = text.trim();
  if (value.length <= max) return value;
  return `${value.slice(0, max).trim()}...`;
}

function formatJson(value: Record<string, unknown>) {
  if (!Object.keys(value).length) return "{}";
  return JSON.stringify(value, null, 2);
}
</script>

<template>
  <div class="memory-page">
    <div class="page-header">
      <div>
        <h1>Memory</h1>
        <div class="header-meta">
          <span>{{ items.length }} active items</span>
          <span>{{ restrictedCount }} restricted</span>
          <span>{{ percent(averageImportance) }} avg importance</span>
        </div>
      </div>
      <div class="header-actions">
        <Button
          size="sm"
          variant="outline"
          title="Select an exact scope type and ID before projecting"
          :disabled="projectMut.isPending.value || !hasExactScope"
          @click="projectCurrentScope()"
        >
          <RefreshCw :size="15" /> Project
        </Button>
        <Button
          size="sm"
          title="Select an exact scope type and ID before creating an item"
          :disabled="!hasExactScope"
          @click="openCreate()"
        >
          <Plus :size="15" /> New
        </Button>
      </div>
    </div>

    <div class="toolbar">
      <div class="tab-buttons">
        <button :class="{ active: activeTab === 'items' }" type="button" @click="activeTab = 'items'">
          <Database :size="14" /> Items
        </button>
        <button :class="{ active: activeTab === 'candidates' }" type="button" @click="activeTab = 'candidates'">
          <Search :size="14" /> Candidates
        </button>
        <button :class="{ active: activeTab === 'turns' }" type="button" @click="activeTab = 'turns'">
          <FileClock :size="14" /> Turns
        </button>
      </div>

      <div class="scope-controls">
        <select v-model="scopeType" class="select-control">
          <option value="">all types</option>
          <option value="global">global</option>
          <option value="chat">chat</option>
          <option value="account">account</option>
          <option value="person">person</option>
        </select>
        <Input v-model="scopeId" placeholder="all scope ids" />
      </div>
    </div>

    <Alert v-if="itemsError" variant="destructive">
      Failed to load memory items: {{ itemsError.message }}
    </Alert>
    <Alert v-if="candidatesQuery.error.value" variant="destructive">
      Failed to load candidates: {{ candidatesQuery.error.value.message }}
    </Alert>
    <Alert v-if="turnsQuery.error.value" variant="destructive">
      Failed to load turns: {{ turnsQuery.error.value.message }}
    </Alert>

    <section v-if="activeTab === 'items'" class="items-layout">
      <aside class="items-panel">
        <div class="filter-row">
          <Input
            v-model="itemSearch"
            placeholder="Search memory"
            @keydown.enter="refetchItems()"
          />
          <Button
            size="icon"
            variant="outline"
            title="Refresh memory items"
            :disabled="itemsFetching"
            @click="refetchItems()"
          >
            <RefreshCw :size="15" />
          </Button>
        </div>

        <div v-if="itemsLoading" class="loading-state">
          <Spinner /> <span>Loading memory...</span>
        </div>
        <div v-else-if="items.length === 0" class="empty-state">
          No memory items found.
        </div>
        <div v-else class="item-list">
          <article
            v-for="item in items"
            :key="item.item_id"
            role="button"
            tabindex="0"
            class="item-row"
            :class="{ selected: selectedItemId === item.item_id }"
            @click="selectedItemId = item.item_id"
            @keydown.enter="selectedItemId = item.item_id"
            @keydown.space.prevent="selectedItemId = item.item_id"
          >
            <div class="item-row-top">
              <span class="item-row-title">{{ itemTitle(item) }}</span>
              <Badge :variant="sensitivityVariant(item.sensitivity)">
                {{ item.sensitivity }}
              </Badge>
            </div>
            <div class="item-row-meta">
              <Badge variant="outline">{{ item.kind }}</Badge>
              <code>{{ scopeLabel(item) }}</code>
              <span>{{ percent(item.importance) }} importance</span>
              <span :title="formatDateTime(itemTime(item))">{{ relativeTime(itemTime(item)) }}</span>
            </div>
            <p>{{ truncate(item.content, 180) }}</p>
          </article>
        </div>
      </aside>

      <main class="detail-panel">
        <div v-if="!selectedItem" class="empty-state">
          Select a memory item.
        </div>
        <template v-else>
          <div class="detail-header">
            <div class="detail-title-block">
              <h2>{{ itemTitle(selectedItem) }}</h2>
              <div class="detail-badges">
                <Badge :variant="sensitivityVariant(selectedItem.sensitivity)">
                  {{ selectedItem.sensitivity }}
                </Badge>
                <Badge variant="outline">{{ selectedItem.sensitivity_source }}</Badge>
                <Badge variant="secondary">{{ selectedItem.kind }}</Badge>
                <Badge variant="outline">{{ selectedItem.source }}</Badge>
              </div>
            </div>
            <Button size="sm" variant="outline" @click="openArchive(selectedItem)">
              <Archive :size="14" /> Archive
            </Button>
          </div>

          <pre class="memory-content">{{ selectedItem.content }}</pre>

          <div class="detail-grid">
            <div>
              <span>Item ID</span>
              <code>{{ selectedItem.item_id }}</code>
            </div>
            <div>
              <span>Scope</span>
              <code>{{ scopeLabel(selectedItem) }}</code>
            </div>
            <div>
              <span>Confidence</span>
              <code>{{ percent(selectedItem.confidence) }}</code>
            </div>
            <div>
              <span>Importance</span>
              <code>{{ percent(selectedItem.importance) }}</code>
            </div>
            <div>
              <span>Created</span>
              <code>{{ formatDateTime(selectedItem.created_at) }}</code>
            </div>
            <div>
              <span>Updated</span>
              <code>{{ formatDateTime(selectedItem.updated_at) }}</code>
            </div>
            <div v-if="selectedItem.parent_id">
              <span>Parent</span>
              <code>{{ selectedItem.parent_id }}</code>
            </div>
            <div v-if="selectedItem.path">
              <span>Path</span>
              <code>{{ selectedItem.path }}</code>
            </div>
          </div>

          <div class="json-columns">
            <div>
              <h3>Evidence</h3>
              <pre>{{ formatJson(selectedItem.evidence) }}</pre>
            </div>
            <div>
              <h3>Metadata</h3>
              <pre>{{ formatJson(selectedItem.metadata) }}</pre>
            </div>
          </div>
        </template>
      </main>
    </section>

    <section v-else-if="activeTab === 'candidates'" class="single-panel">
      <div class="filter-row wide">
        <select v-model="candidateStatus" class="select-control">
          <option value="">Any status</option>
          <option value="pending">pending</option>
          <option value="auto_applied">auto_applied</option>
          <option value="rejected">rejected</option>
        </select>
        <span class="result-count">
          {{ candidatesQuery.data.value?.total ?? 0 }} candidates
        </span>
      </div>

      <div v-if="candidatesQuery.isLoading.value" class="loading-state">
        <Spinner /> <span>Loading candidates...</span>
      </div>
      <div v-else-if="!(candidatesQuery.data.value?.candidates ?? []).length" class="empty-state">
        No candidates found.
      </div>
      <div v-else class="candidate-list">
        <article
          v-for="candidate in candidatesQuery.data.value?.candidates ?? []"
          :key="candidate.candidate_id"
          class="candidate-row"
        >
          <div class="candidate-header">
            <Badge :variant="candidateVariant(candidate.status)">
              {{ candidate.status }}
            </Badge>
            <strong>{{ candidate.title || candidate.kind }}</strong>
            <Badge variant="outline">{{ candidate.kind }}</Badge>
            <span :title="formatDateTime(candidate.updated_at)">
              {{ relativeTime(candidate.updated_at) }}
            </span>
          </div>
          <p>{{ candidate.content }}</p>
          <div class="candidate-meta">
            <code>{{ candidate.candidate_id }}</code>
            <code>{{ candidate.scope_type }}:{{ candidate.scope_id }}</code>
            <span>{{ percent(candidate.confidence) }} confidence</span>
          </div>
        </article>
      </div>
    </section>

    <section v-else class="single-panel">
      <div class="turn-filters">
        <Input v-model="turnSearch" placeholder="Search turns" />
        <Input v-model="turnChatAddress" placeholder="chat address" />
        <Input v-model="turnSource" placeholder="source" />
        <select v-model="turnRole" class="select-control">
          <option value="">Any role</option>
          <option value="user">user</option>
          <option value="assistant">assistant</option>
          <option value="system">system</option>
        </select>
        <Button
          size="icon"
          variant="outline"
          title="Refresh turns"
          :disabled="turnsQuery.isFetching.value"
          @click="turnsQuery.refetch()"
        >
          <RefreshCw :size="15" />
        </Button>
      </div>

      <div v-if="turnsQuery.isLoading.value" class="loading-state">
        <Spinner /> <span>Loading turns...</span>
      </div>
      <div v-else-if="!(turnsQuery.data.value?.turns ?? []).length" class="empty-state">
        No turns found.
      </div>
      <div v-else class="turn-list">
        <article
          v-for="turn in turnsQuery.data.value?.turns ?? []"
          :key="turn.turn_id"
          class="turn-row"
        >
          <div class="turn-header">
            <Badge :variant="roleVariant(turn.role)">{{ turn.role }}</Badge>
            <Badge v-if="turn.source" variant="outline">{{ turn.source }}</Badge>
            <code>{{ turn.session_id }}</code>
            <span :title="formatDateTime(turn.created_at)">
              {{ relativeTime(turn.created_at) }}
            </span>
          </div>
          <pre>{{ turn.content }}</pre>
          <div v-if="turn.keywords.length" class="keyword-row">
            <Badge v-for="keyword in turn.keywords.slice(0, 12)" :key="keyword" variant="secondary">
              {{ keyword }}
            </Badge>
          </div>
        </article>
      </div>
    </section>

    <ConfirmDialog
      :open="showCreateDialog"
      title="New Memory Item"
      confirm-label="Create"
      :loading="createMut.isPending.value"
      :disabled="createDisabled"
      size="wide"
      @confirm="doCreate()"
      @update:open="showCreateDialog = $event"
    >
      <div class="dialog-form">
        <div class="form-grid">
          <label>
            <span>Title</span>
            <Input v-model="newTitle" placeholder="Short title" />
          </label>
          <label>
            <span>Kind</span>
            <select v-model="newKind" class="select-control full">
              <option value="fact">fact</option>
              <option value="preference">preference</option>
              <option value="task">task</option>
              <option value="decision">decision</option>
              <option value="observation">observation</option>
            </select>
          </label>
          <label>
            <span>Sensitivity</span>
            <select v-model="newSensitivity" class="select-control full">
              <option value="public">public</option>
              <option value="private">private</option>
              <option value="secret_like">secret_like</option>
            </select>
          </label>
          <label>
            <span>Confidence</span>
            <input v-model.number="newConfidence" class="number-input" type="number" min="0" max="1" step="0.05" />
          </label>
          <label>
            <span>Importance</span>
            <input v-model.number="newImportance" class="number-input" type="number" min="0" max="1" step="0.05" />
          </label>
          <label class="checkbox-row">
            <input v-model="newProject" type="checkbox" />
            <span>Project after create</span>
          </label>
        </div>
        <label>
          <span>Content</span>
          <Textarea v-model="newContent" :rows="8" placeholder="Memory content" />
        </label>
      </div>
    </ConfirmDialog>

    <ConfirmDialog
      :open="showArchiveDialog"
      title="Archive Memory Item"
      variant="destructive"
      confirm-label="Archive"
      :loading="archiveMut.isPending.value"
      @confirm="doArchive()"
      @update:open="showArchiveDialog = $event"
    >
      <p class="archive-copy">
        Archive <strong>{{ archiveTarget ? itemTitle(archiveTarget) : "" }}</strong>?
      </p>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.memory-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  min-height: 0;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.page-header h1 {
  margin: 0;
  color: var(--color-foreground);
  font-size: 1.25rem;
  font-weight: 600;
}

.header-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.25rem;
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

.header-actions,
.toolbar,
.tab-buttons,
.scope-controls,
.filter-row,
.turn-filters {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.toolbar {
  justify-content: space-between;
  flex-wrap: wrap;
}

.tab-buttons {
  padding: 0.125rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-card);
}

.tab-buttons button {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  min-height: 30px;
  padding: 0 0.7rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted-foreground);
  cursor: pointer;
  font-size: 0.75rem;
}

.tab-buttons button:hover,
.tab-buttons button.active {
  background: var(--color-accent);
  color: var(--color-foreground);
}

.scope-controls {
  min-width: min(24rem, 100%);
}

.scope-controls :deep(.input) {
  min-width: 12rem;
}

.select-control,
.number-input {
  min-height: 32px;
  padding: 0 0.5rem;
  border: 1px solid var(--color-input);
  border-radius: var(--radius-md);
  background: var(--color-background);
  color: var(--color-foreground);
  font-size: 0.8125rem;
}

.select-control.full,
.number-input {
  width: 100%;
}

.items-layout {
  display: grid;
  grid-template-columns: minmax(300px, 380px) minmax(0, 1fr);
  gap: 1rem;
  min-height: 0;
}

.items-panel,
.detail-panel,
.single-panel {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-card);
}

.items-panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-height: calc(100vh - 170px);
  padding: 0.75rem;
}

.detail-panel {
  max-height: calc(100vh - 170px);
  overflow-y: auto;
  padding: 1rem;
}

.single-panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-height: calc(100vh - 170px);
  overflow-y: auto;
  padding: 0.75rem;
}

.filter-row {
  flex-shrink: 0;
}

.filter-row :deep(.input) {
  min-width: 0;
}

.filter-row.wide {
  justify-content: space-between;
}

.result-count,
.loading-state,
.empty-state {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 1rem;
}

.empty-state {
  padding: 2rem;
  text-align: center;
}

.item-list,
.candidate-list,
.turn-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-height: 0;
  overflow-y: auto;
}

.item-list {
  flex: 1 1 auto;
}

.item-row {
  width: 100%;
  flex: 0 0 auto;
  min-height: 118px;
  padding: 0.65rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-foreground);
  cursor: pointer;
  text-align: left;
  outline: none;
  transition:
    background 0.15s,
    border-color 0.15s,
    box-shadow 0.15s;
}

.item-row:hover,
.item-row.selected {
  border-color: color-mix(in srgb, var(--color-primary) 42%, var(--color-border));
  background: var(--color-accent);
}

.item-row:focus-visible {
  border-color: color-mix(in srgb, var(--color-primary) 54%, var(--color-border));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-ring) 22%, transparent);
}

.item-row-top,
.item-row-meta,
.candidate-header,
.candidate-meta,
.turn-header,
.keyword-row,
.detail-badges {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.item-row-top {
  justify-content: space-between;
  gap: 0.75rem;
}

.item-row-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 0.875rem;
}

.item-row-meta,
.candidate-meta,
.turn-header {
  margin-top: 0.35rem;
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

.item-row p,
.candidate-row p {
  margin: 0.5rem 0 0;
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.item-row p {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--color-border);
}

.detail-title-block {
  min-width: 0;
}

.detail-title-block h2 {
  margin: 0 0 0.45rem;
  overflow-wrap: anywhere;
  font-size: 1rem;
  font-weight: 600;
}

.memory-content {
  margin: 1rem 0;
  padding: 0.85rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-background);
  color: var(--color-foreground);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: inherit;
  font-size: 0.875rem;
  line-height: 1.55;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.75rem;
}

.detail-grid div {
  min-width: 0;
}

.detail-grid span,
.json-columns h3,
.dialog-form label > span {
  display: block;
  margin-bottom: 0.25rem;
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
  font-weight: 600;
}

.detail-grid code,
.candidate-meta code,
.turn-header code {
  display: inline-block;
  max-width: 100%;
  padding: 0.125rem 0.35rem;
  border-radius: var(--radius-sm);
  background: var(--color-muted);
  color: var(--color-foreground);
  overflow-wrap: anywhere;
  font-size: 0.75rem;
}

.json-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  margin-top: 1rem;
}

.json-columns pre,
.turn-row pre {
  margin: 0;
  padding: 0.75rem;
  border-radius: var(--radius-md);
  background: var(--color-background);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.75rem;
  line-height: 1.5;
}

.candidate-row,
.turn-row {
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.candidate-header strong {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 0.875rem;
}

.turn-filters {
  display: grid;
  grid-template-columns: minmax(140px, 2fr) minmax(140px, 1.5fr) minmax(100px, 1fr) 120px 34px;
}

.turn-row pre {
  margin-top: 0.5rem;
  font-family: inherit;
  font-size: 0.8125rem;
}

.keyword-row {
  margin-top: 0.5rem;
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
}

.checkbox-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-top: 1.3rem;
}

.checkbox-row span {
  margin: 0;
}

.archive-copy {
  margin: 0;
  overflow-wrap: anywhere;
}

@media (max-width: 960px) {
  .items-layout,
  .json-columns {
    grid-template-columns: 1fr;
  }

  .items-panel,
  .detail-panel,
  .single-panel {
    max-height: none;
  }

  .turn-filters,
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
