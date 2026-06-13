<script setup lang="ts">
import { computed, ref } from "vue";
import {
  BookOpen,
  Plus,
  Search,
  Trash2,
  Upload,
  FileText,
} from "lucide-vue-next";
import {
  useKbCollections,
  useKbCreateCollection,
  useKbDeleteCollection,
  useKbImportText,
  useKbImportFile,
  useKbSearch,
} from "@/api/queries";
import type { KbCollectionSummary, KbDocumentResponse } from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";
import Input from "@/components/ui/Input.vue";
import Textarea from "@/components/ui/Textarea.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import Spinner from "@/components/ui/Spinner.vue";

// ── State ────────────────────────────────────────────

const showCreateDialog = ref(false);
const newCollectionName = ref("");
const showImportDialog = ref(false);
const importCollection = ref("");
const importSource = ref("");
const importContent = ref("");
const importContentType = ref<"markdown" | "text">("markdown");
const importTab = ref<"text" | "file">("text");
const importFile = ref<File | null>(null);
const showDeleteDialog = ref(false);
const deleteTarget = ref("");
const showSearchDialog = ref(false);
const searchCollection = ref("");
const searchQuery = ref("");
const searchResults = ref<KbDocumentResponse[]>([]);
const hasSearched = ref(false);

// ── Queries & Mutations ──────────────────────────────

const {
  data: collectionsData,
  isLoading,
  error,
} = useKbCollections();
const createMut = useKbCreateCollection();
const deleteMut = useKbDeleteCollection();
const importTextMut = useKbImportText();
const importFileMut = useKbImportFile();
const searchMut = useKbSearch();

const collections = computed<KbCollectionSummary[]>(
  () => collectionsData.value?.collections ?? [],
);

const COLLECTION_NAME_PATTERN = /^[A-Za-z0-9_]+$/;

function collectionNameError(name: string): string {
  const normalized = name.trim();
  if (!normalized || COLLECTION_NAME_PATTERN.test(normalized)) return "";
  return "Use only letters, digits, and underscores.";
}

const newCollectionError = computed(() =>
  collectionNameError(newCollectionName.value),
);
const importCollectionError = computed(() =>
  collectionNameError(importCollection.value),
);
const searchCollectionError = computed(() =>
  collectionNameError(searchCollection.value),
);
const createDisabled = computed(
  () => !newCollectionName.value.trim() || !!newCollectionError.value,
);
const importDisabled = computed(
  () =>
    !importCollection.value.trim()
    || !!importCollectionError.value
    || (importTab.value === "text"
      ? !importContent.value.trim()
      : !importFile.value),
);
const searchDisabled = computed(
  () =>
    !searchCollection.value.trim()
    || !!searchCollectionError.value
    || !searchQuery.value.trim(),
);

// ── Create ───────────────────────────────────────────

function openCreate() {
  newCollectionName.value = "";
  showCreateDialog.value = true;
}

async function doCreate() {
  const name = newCollectionName.value.trim();
  if (!name || collectionNameError(name)) return;
  try {
    await createMut.mutateAsync(name);
    showCreateDialog.value = false;
    newCollectionName.value = "";
  } catch {
    // The mutation displays the API error and keeps the dialog open.
  }
}

// ── Delete ───────────────────────────────────────────

function openDelete(name: string) {
  deleteTarget.value = name;
  showDeleteDialog.value = true;
}

async function doDelete() {
  if (!deleteTarget.value) return;
  try {
    await deleteMut.mutateAsync(deleteTarget.value);
    showDeleteDialog.value = false;
    deleteTarget.value = "";
  } catch {
    // The mutation displays the API error and keeps the dialog open.
  }
}

// ── Import Text ──────────────────────────────────────

function openImport(collectionName?: string) {
  importCollection.value = collectionName ?? "";
  importSource.value = "";
  importContent.value = "";
  importContentType.value = "markdown";
  importFile.value = null;
  importTab.value = "text";
  showImportDialog.value = true;
}

async function doImportText() {
  const coll = importCollection.value.trim();
  const src = importSource.value.trim() || "Untitled";
  const content = importContent.value.trim();
  if (!coll || collectionNameError(coll) || !content) return;
  try {
    await importTextMut.mutateAsync({
      collection: coll,
      source: src,
      content,
      contentType: importContentType.value,
    });
    showImportDialog.value = false;
  } catch {
    // The mutation displays the API error and keeps the dialog open.
  }
}

async function doImportFile() {
  const coll = importCollection.value.trim();
  const file = importFile.value;
  if (!coll || collectionNameError(coll) || !file) return;
  try {
    await importFileMut.mutateAsync({ collection: coll, file });
    showImportDialog.value = false;
  } catch {
    // The mutation displays the API error and keeps the dialog open.
  }
}

function onFileSelected(event: Event) {
  const target = event.target as HTMLInputElement;
  importFile.value = target.files?.[0] ?? null;
}

// ── Search ───────────────────────────────────────────

function openSearch(collectionName?: string) {
  searchCollection.value = collectionName ?? "";
  searchQuery.value = "";
  searchResults.value = [];
  hasSearched.value = false;
  showSearchDialog.value = true;
}

async function doSearch() {
  const coll = searchCollection.value.trim();
  const q = searchQuery.value.trim();
  if (!coll || collectionNameError(coll) || !q) return;
  searchResults.value = [];
  hasSearched.value = false;
  try {
    const resp = await searchMut.mutateAsync({
      collection: coll,
      query: q,
      limit: 10,
    });
    searchResults.value = resp.results;
    hasSearched.value = true;
  } catch {
    // The mutation displays the API error; old results remain cleared.
  }
}

const isImporting = computed(
  () => importTextMut.isPending.value || importFileMut.isPending.value,
);
</script>

<template>
  <div class="kb-page">
    <div class="page-header">
      <h1>Knowledge Base</h1>
      <div class="header-actions">
        <Button size="sm" @click="openCreate()">
          <Plus :size="16" /> New Collection
        </Button>
        <Button size="sm" variant="outline" @click="openImport()">
          <Upload :size="16" /> Import
        </Button>
      </div>
    </div>

    <!-- Error -->
    <Alert v-if="error" variant="destructive">
      Failed to load collections: {{ error.message }}
    </Alert>

    <!-- Loading -->
    <div v-if="isLoading" class="loading-state">
      <Spinner />
      <span>Loading collections...</span>
    </div>

    <!-- Empty -->
    <div v-else-if="collections.length === 0" class="empty-state">
      <BookOpen :size="48" class="empty-icon" />
      <h2>No knowledge base collections</h2>
      <p>Import documents to create a collection and supplement the bot's knowledge.</p>
    </div>

    <!-- Collection grid -->
    <div v-else class="collection-grid">
      <Card
        v-for="coll in collections"
        :key="coll.name"
        class="collection-card"
      >
        <div class="card-header">
          <BookOpen :size="18" class="card-icon" />
          <span class="card-title">{{ coll.name }}</span>
          <Badge variant="secondary">{{ coll.document_count }} chunks</Badge>
        </div>
        <div v-if="coll.created_at" class="card-meta">
          Created {{ coll.created_at }}
        </div>
        <div class="card-actions">
          <Button size="sm" variant="outline" @click="openImport(coll.name)">
            <Upload :size="14" /> Import
          </Button>
          <Button size="sm" variant="outline" @click="openSearch(coll.name)">
            <Search :size="14" /> Search
          </Button>
          <Button
            size="sm"
            variant="ghost"
            @click="openDelete(coll.name)"
          >
            <Trash2 :size="14" />
          </Button>
        </div>
      </Card>
    </div>

    <!-- Create Dialog -->
    <ConfirmDialog
      :open="showCreateDialog"
      title="Create Collection"
      confirm-label="Create"
      :loading="createMut.isPending.value"
      :disabled="createDisabled"
      @confirm="doCreate()"
      @update:open="showCreateDialog = $event"
    >
      <div class="dialog-form">
        <label class="form-label">Collection Name</label>
        <Input
          v-model="newCollectionName"
          placeholder="e.g. python_docs"
          @keydown.enter="doCreate()"
        />
        <p v-if="newCollectionError" class="field-error">
          {{ newCollectionError }}
        </p>
      </div>
    </ConfirmDialog>

    <!-- Delete Dialog -->
    <ConfirmDialog
      :open="showDeleteDialog"
      title="Delete Collection"
      variant="destructive"
      confirm-label="Delete"
      :loading="deleteMut.isPending.value"
      @confirm="doDelete()"
      @update:open="showDeleteDialog = $event"
    >
      <p>
        Delete collection <strong>{{ deleteTarget }}</strong
        >? All documents will be permanently removed.
      </p>
    </ConfirmDialog>

    <!-- Import Dialog -->
    <ConfirmDialog
      :open="showImportDialog"
      title="Import into Knowledge Base"
      confirm-label="Import"
      :loading="isImporting"
      :disabled="importDisabled"
      @confirm="importTab === 'text' ? doImportText() : doImportFile()"
      @update:open="showImportDialog = $event"
    >
      <div class="dialog-form">
        <label class="form-label">Collection</label>
        <Input v-model="importCollection" placeholder="e.g. python_docs" />
        <p v-if="importCollectionError" class="field-error">
          {{ importCollectionError }}
        </p>

        <div class="tab-row">
          <button
            class="tab-btn"
            :class="{ active: importTab === 'text' }"
            @click="importTab = 'text'"
          >
            <FileText :size="14" /> Text
          </button>
          <button
            class="tab-btn"
            :class="{ active: importTab === 'file' }"
            @click="importTab = 'file'"
          >
            <Upload :size="14" /> File Upload
          </button>
        </div>

        <template v-if="importTab === 'text'">
          <label class="form-label">Title / Source</label>
          <Input v-model="importSource" placeholder="e.g. AsyncIO Guide" />

          <label class="form-label">Content Format</label>
          <select v-model="importContentType" class="format-select">
            <option value="markdown">Markdown</option>
            <option value="text">Plain text</option>
          </select>

          <label class="form-label">Content</label>
          <Textarea
            v-model="importContent"
            :rows="8"
            placeholder="Paste text or Markdown content here..."
          />
        </template>

        <template v-else>
          <label class="form-label">Upload File (.md, .txt)</label>
          <input
            type="file"
            class="file-input"
            accept=".md,.markdown,.txt,.text"
            @change="onFileSelected($event)"
          />
          <p v-if="importFile" class="file-info">
            Selected: {{ importFile.name }} ({{
              (importFile.size / 1024).toFixed(1)
            }}
            KB)
          </p>
        </template>
      </div>
    </ConfirmDialog>

    <!-- Search Dialog -->
    <ConfirmDialog
      :open="showSearchDialog"
      title="Search Knowledge Base"
      confirm-label="Search"
      :loading="searchMut.isPending.value"
      :disabled="searchDisabled"
      @confirm="doSearch()"
      @update:open="showSearchDialog = $event"
    >
      <div class="dialog-form">
        <label class="form-label">Collection</label>
        <Input v-model="searchCollection" placeholder="Collection name" />
        <p v-if="searchCollectionError" class="field-error">
          {{ searchCollectionError }}
        </p>

        <label class="form-label">Query</label>
        <Input
          v-model="searchQuery"
          placeholder="Search terms..."
          @keydown.enter="doSearch()"
        />

        <!-- Search Results -->
        <div v-if="hasSearched" class="search-results">
          <p v-if="searchResults.length === 0" class="no-results">
            No results found.
          </p>
          <div
            v-for="r in searchResults"
            :key="r.doc_id"
            class="result-item"
          >
            <div class="result-header">
              <strong>{{ r.title }}</strong>
              <Badge variant="outline">{{ r.doc_id }}</Badge>
            </div>
            <p class="result-content">
              {{ r.content.slice(0, 300) }}{{ r.content.length > 300 ? "..." : "" }}
            </p>
          </div>
        </div>
      </div>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.kb-page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-header h1 {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--color-foreground);
  margin: 0;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.loading-state {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  color: var(--color-muted-foreground);
  padding: 2rem 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.75rem;
  padding: 4rem 0;
  text-align: center;
  color: var(--color-muted-foreground);
}

.empty-state h2 {
  font-size: 1.125rem;
  font-weight: 500;
  color: var(--color-foreground);
  margin: 0;
}

.empty-state p {
  margin: 0;
  max-width: 28rem;
}

.empty-icon {
  opacity: 0.4;
}

.collection-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
}

.collection-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.card-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}

.card-title {
  font-weight: 600;
  font-size: 0.9375rem;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-meta {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.card-actions {
  display: flex;
  gap: 0.375rem;
  margin-top: auto;
  padding-top: 0.25rem;
  border-top: 1px solid var(--color-border);
}

/* Dialog form styles */
.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-width: 20rem;
}

.form-label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-foreground);
}

.field-error {
  margin: -0.5rem 0 0;
  color: var(--color-destructive);
  font-size: 0.75rem;
}

.format-select {
  width: 100%;
  min-height: 2.25rem;
  padding: 0.375rem 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-background);
  color: var(--color-foreground);
  font-size: 0.8125rem;
}

.tab-row {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 0.25rem;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.75rem;
  border: none;
  background: none;
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color 0.15s, border-color 0.15s;
}

.tab-btn:hover {
  color: var(--color-foreground);
}

.tab-btn.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.file-input {
  font-size: 0.8125rem;
  color: var(--color-foreground);
}

.file-input::file-selector-button {
  margin-right: 0.5rem;
  padding: 0.25rem 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-accent);
  color: var(--color-foreground);
  cursor: pointer;
  font-size: 0.8125rem;
}

.file-info {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  margin: 0;
}

.search-results {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  max-height: 20rem;
  overflow-y: auto;
}

.no-results {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  margin: 0;
  padding: 0.5rem 0;
}

.result-item {
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.result-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
}

.result-header strong {
  font-size: 0.8125rem;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-content {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  margin: 0;
  line-height: 1.5;
}
</style>
