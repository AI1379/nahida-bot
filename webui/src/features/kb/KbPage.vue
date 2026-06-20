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
  useKbCollectionStatuses,
  useKbCreateCollection,
  useKbDeleteCollection,
  useKbDocuments,
  useKbImportText,
  useKbImportFiles,
  useKbSearch,
} from "@/api/queries";
import type {
  KbBatchImportResponse,
  KbCollectionSummary,
  KbDocumentResponse,
} from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";
import FileDropZone from "@/components/ui/FileDropZone.vue";
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
const importFiles = ref<File[]>([]);
const batchImportResult = ref<KbBatchImportResponse | null>(null);
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
const importFilesMut = useKbImportFiles();
const searchMut = useKbSearch();

const collections = computed<KbCollectionSummary[]>(
  () => collectionsData.value?.collections ?? [],
);
const collectionStatuses = useKbCollectionStatuses(
  computed(() => collections.value.map((collection) => collection.name)),
);

const COLLECTION_NAME_PATTERN = /^[A-Za-z0-9_]+$/;
const MAX_IMPORT_FILES = 200;
const MAX_IMPORT_FILE_BYTES = 25 * 1024 * 1024;
const KB_DOCUMENT_ACCEPT = [
  ".md",
  ".markdown",
  ".txt",
  ".text",
  ".pdf",
  ".docx",
  ".pptx",
  ".xls",
  ".xlsx",
  ".html",
  ".htm",
  ".csv",
  ".json",
  ".xml",
  ".epub",
  ".msg",
  ".ipynb",
].join(",");

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
      : importFiles.value.length === 0),
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
  importFiles.value = [];
  batchImportResult.value = null;
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

async function doImportFiles() {
  const coll = importCollection.value.trim();
  const files = importFiles.value;
  if (!coll || collectionNameError(coll) || files.length === 0) return;
  try {
    const resp = await importFilesMut.mutateAsync({ collection: coll, files });
    batchImportResult.value = resp;
    if (resp.failed_files === 0) {
      showImportDialog.value = false;
      importFiles.value = [];
      return;
    }
    importFiles.value = files.filter(
      (_, index) => resp.results[index]?.status === "failed",
    );
  } catch {
    // The mutation displays the API error and keeps the dialog open.
  }
}

function setImportFiles(files: File[]) {
  importFiles.value = files;
  batchImportResult.value = null;
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
  () => importTextMut.isPending.value || importFilesMut.isPending.value,
);

// ── Browse ────────────────────────────────────────────

const showBrowsePanel = ref(false);
const browseCollection = ref<string | null>(null);
const browsePageOffset = ref(0);
const BROWSE_LIMIT = 50;

const {
  data: browseDataRaw,
  isLoading: browseLoading,
} = useKbDocuments(browseCollection, {
  limit: BROWSE_LIMIT,
  offset: browsePageOffset,
});

const browseTotal = computed(() => browseDataRaw.value?.total ?? 0);
const browseTotalPages = computed(() =>
  Math.max(1, Math.ceil(browseTotal.value / BROWSE_LIMIT)),
);
const browseStatus = computed(() => {
  const collection = browseCollection.value;
  return collection ? collectionStatuses.value[collection] ?? "idle" : null;
});

function openBrowse(collectionName: string) {
  browseCollection.value = collectionName;
  browsePageOffset.value = 0;
  showBrowsePanel.value = true;
}

function browsePrevPage() {
  if (browsePageOffset.value > 0) {
    browsePageOffset.value = Math.max(0, browsePageOffset.value - BROWSE_LIMIT);
  }
}

function browseNextPage() {
  if (browsePageOffset.value + BROWSE_LIMIT < browseTotal.value) {
    browsePageOffset.value += BROWSE_LIMIT;
  }
}

function embeddingStatusLabel(status: string) {
  if (status === "embedding") return "Embedding...";
  if (status === "embedded") return "Vector ready";
  return "Idle";
}
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
          <Badge
            v-if="collectionStatuses[coll.name] !== 'idle'"
            :variant="collectionStatuses[coll.name] === 'embedding' ? 'default' : collectionStatuses[coll.name] === 'embedded' ? 'secondary' : 'outline'"
          >
            {{ embeddingStatusLabel(collectionStatuses[coll.name]) }}
          </Badge>
        </div>
        <div v-if="coll.created_at" class="card-meta">
          Created {{ coll.created_at }}
        </div>
        <div class="card-actions">
          <Button size="sm" variant="outline" @click="openImport(coll.name)">
            <Upload :size="14" /> Import
          </Button>
          <Button size="sm" variant="outline" @click="openBrowse(coll.name)">
            <BookOpen :size="14" /> Browse
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
      @confirm="importTab === 'text' ? doImportText() : doImportFiles()"
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
            <Upload :size="14" /> Files
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
          <label class="form-label">Upload Document</label>
          <FileDropZone
            :model-value="importFiles"
            :accept="KB_DOCUMENT_ACCEPT"
            :max-files="MAX_IMPORT_FILES"
            :max-size-bytes="MAX_IMPORT_FILE_BYTES"
            :disabled="isImporting"
            @update:model-value="setImportFiles"
          />
          <p class="file-help">
            Text and Markdown work by default. PDF, Word, PowerPoint, Excel,
            HTML, EPUB, and other rich formats require the server's
            document-import extra.
          </p>
          <div v-if="batchImportResult" class="batch-results">
            <p class="batch-summary">
              {{ batchImportResult.imported_files }} imported,
              {{ batchImportResult.failed_files }} failed,
              {{ batchImportResult.chunks }} chunks.
            </p>
            <div
              v-for="(result, index) in batchImportResult.results.filter((item) => item.status === 'failed')"
              :key="`${result.source}:${index}`"
              class="batch-error"
            >
              <Badge variant="destructive">{{ result.source }}</Badge>
              <span>{{ result.error }}</span>
            </div>
          </div>
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
              <Badge variant="outline">{{ r.node_type }}</Badge>
            </div>
            <div v-if="r.path" class="result-path">
              <code>{{ r.path }}</code>
            </div>
            <div class="result-meta-line">
              <span class="result-id">{{ r.doc_id }}</span>
              <span v-if="r.source_id" class="result-source">← {{ r.source_id }}</span>
            </div>
            <p class="result-content">
              {{ r.content.slice(0, 300) }}{{ r.content.length > 300 ? "..." : "" }}
            </p>
          </div>
        </div>
      </div>
    </ConfirmDialog>

    <!-- Browse Panel -->
    <ConfirmDialog
      :open="showBrowsePanel"
      title="Browse Documents"
      confirm-label="Close"
      size="wide"
      :show-cancel="false"
      @confirm="showBrowsePanel = false"
      @update:open="showBrowsePanel = $event"
    >
      <div class="browse-dialog-form">
        <div class="browse-info">
          <span>{{ browseCollection }} — {{ browseTotal }} documents</span>
          <Badge
            v-if="browseStatus"
            :variant="browseStatus === 'embedding' ? 'default' : 'outline'"
          >
            {{ embeddingStatusLabel(browseStatus) }}
          </Badge>
        </div>
        <div v-if="browseLoading" class="loading-state">
          <Spinner /> <span>Loading...</span>
        </div>
        <div v-else class="browse-table-container">
          <table class="browse-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Type</th>
                <th>Path</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="doc in browseDataRaw?.documents ?? []"
                :key="doc.doc_id"
                class="browse-row"
              >
                <td class="browse-title">{{ doc.title }}</td>
                <td><Badge variant="outline" size="sm">{{ doc.node_type }}</Badge></td>
                <td class="browse-path"><code>{{ doc.path || '-' }}</code></td>
                <td class="browse-source">{{ doc.source_id || '-' }}</td>
              </tr>
              <tr v-if="(browseDataRaw?.documents ?? []).length === 0">
                <td colspan="4" class="no-results">No documents.</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="browse-pagination">
          <Button
            size="sm" variant="outline"
            :disabled="browsePageOffset === 0"
            @click="browsePrevPage()"
          >
            Previous
          </Button>
          <span class="browse-page-info">
            {{ Math.floor(browsePageOffset / BROWSE_LIMIT) + 1 }} /
            {{ browseTotalPages }}
          </span>
          <Button
            size="sm" variant="outline"
            :disabled="browsePageOffset + BROWSE_LIMIT >= browseTotal"
            @click="browseNextPage()"
          >
            Next
          </Button>
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

.file-help {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  line-height: 1.45;
  margin: -0.25rem 0 0;
}

.batch-results {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  padding-top: 0.25rem;
}

.batch-summary {
  margin: 0;
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

.batch-error {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.75rem;
  line-height: 1.4;
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

.result-path {
  margin-bottom: 0.125rem;
}

.result-path code {
  font-size: 0.6875rem;
  color: var(--color-primary);
  background: none;
  padding: 0;
}

.result-meta-line {
  display: flex;
  gap: 0.5rem;
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
  margin-bottom: 0.25rem;
}

.result-id {
  font-family: monospace;
}

.result-source {
  opacity: 0.7;
}

.result-content {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  margin: 0;
  line-height: 1.5;
}

/* Browse panel */
.browse-dialog-form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: 100%;
  min-width: 0;
}

.browse-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.8125rem;
  color: var(--color-muted-foreground);
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--color-border);
}

.browse-table-container {
  max-height: min(24rem, calc(100dvh - 14rem));
  overflow: auto;
}

.browse-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 0.75rem;
}

.browse-table th:nth-child(1) { width: 25%; }
.browse-table th:nth-child(2) { width: 16%; }
.browse-table th:nth-child(3) { width: 41%; }
.browse-table th:nth-child(4) { width: 18%; }

.browse-table th {
  text-align: left;
  padding: 0.375rem 0.5rem;
  color: var(--color-muted-foreground);
  font-weight: 600;
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  background: var(--color-background);
}

.browse-table td {
  padding: 0.375rem 0.5rem;
  vertical-align: top;
  overflow: hidden;
}

.browse-row:hover {
  background: var(--color-muted);
}

.browse-title {
  font-weight: 500;
  max-width: 12rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.browse-path code {
  display: block;
  font-size: 0.6875rem;
  color: var(--color-primary);
  background: none;
  padding: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.browse-source {
  max-width: 10rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-muted-foreground);
}

.browse-pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--color-border);
}

.browse-page-info {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}
</style>
