<script setup lang="ts">
import { ref, computed } from "vue";
import { useWorkspaces, useFileList, useFileContent } from "@/api/queries";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";

const { data: wsData, isLoading: wsLoading } = useWorkspaces();

const currentPath = ref(".");
const selectedFile = ref("");
const workspaceId = computed(() => wsData.value?.active ?? "default");

const {
  data: fileList,
  isLoading: listLoading,
  error: listError,
} = useFileList(workspaceId, currentPath);

const contentQuery = useFileContent(workspaceId, selectedFile);

const pathParts = computed(() => {
  if (currentPath.value === ".") return [];
  return currentPath.value.split("/").filter(Boolean);
});

function navigateUp() {
  const parts = currentPath.value.split("/").filter(Boolean);
  parts.pop();
  currentPath.value = parts.length ? parts.join("/") : ".";
  selectedFile.value = "";
}

function navigateTo(index: number) {
  const parts = currentPath.value.split("/").filter(Boolean);
  currentPath.value = parts.slice(0, index + 1).join("/") || ".";
  selectedFile.value = "";
}

function enterDir(name: string) {
  currentPath.value =
    currentPath.value === "." ? name : `${currentPath.value}/${name}`;
  selectedFile.value = "";
}

function selectFile(name: string) {
  selectedFile.value =
    currentPath.value === "."
      ? name
      : `${currentPath.value}/${name}`;
}

function isTextFile(name: string) {
  return /\.(md|txt|yaml|yml|json)$/i.test(name);
}

const isMarkdown = computed(() =>
  /\.md$/i.test(selectedFile.value),
);
</script>

<template>
  <div class="files-page">
    <Alert v-if="listError" variant="destructive">
      Failed to load files: {{ listError.message }}
    </Alert>

    <div v-if="wsLoading || listLoading" class="loading">Loading...</div>

    <template v-if="wsData">
      <!-- Workspace selector -->
      <div class="files-toolbar">
        <span class="toolbar-label">Workspace: <strong>{{ wsData.active }}</strong></span>
        <Badge variant="outline">{{ workspaceId }}</Badge>
      </div>

      <!-- Breadcrumb -->
      <div class="breadcrumb">
        <button class="crumb" @click="currentPath = '.'; selectedFile = ''">root</button>
        <template v-for="(part, i) in pathParts" :key="i">
          <span class="crumb-sep">/</span>
          <button class="crumb" @click="navigateTo(i)">{{ part }}</button>
        </template>
      </div>

      <div class="files-layout">
        <!-- File list -->
        <div class="file-list">
          <div v-if="currentPath !== '.'" class="file-item dir-item" @click="navigateUp">
            <span class="file-icon">📁</span>
            <span class="file-name">..</span>
          </div>
          <div
            v-for="entry in fileList?.entries"
            :key="entry.path"
            class="file-item"
            :class="{ selected: selectedFile === (currentPath === '.' ? entry.name : `${currentPath}/${entry.name}`) }"
            @click="entry.is_dir ? enterDir(entry.name) : (isTextFile(entry.name) && selectFile(entry.name))"
          >
            <span class="file-icon">{{ entry.is_dir ? "📁" : "📄" }}</span>
            <span class="file-name">{{ entry.name }}</span>
            <span v-if="!entry.is_dir" class="file-size">{{ entry.size }} B</span>
          </div>
          <div v-if="fileList && !fileList.entries.length" class="empty">Empty directory.</div>
        </div>

        <!-- Preview -->
        <div class="file-preview">
          <div v-if="!selectedFile" class="empty">Select a text file to preview.</div>
          <div v-else-if="contentQuery.isLoading.value" class="loading">Loading...</div>
          <template v-else-if="contentQuery.data.value">
            <div class="preview-header">
              <code>{{ selectedFile }}</code>
              <Badge variant="outline">{{ contentQuery.data.value.size }} B</Badge>
            </div>
            <pre class="preview-content" :class="{ markdown: isMarkdown }">{{ contentQuery.data.value.content }}</pre>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.files-page {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
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

.files-toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.8125rem;
}

.toolbar-label {
  color: var(--color-muted-foreground);
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.8125rem;
}

.crumb {
  background: none;
  border: none;
  color: var(--color-primary);
  cursor: pointer;
  padding: 0.125rem 0.25rem;
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
}

.crumb:hover {
  background: var(--color-accent);
}

.crumb-sep {
  color: var(--color-muted-foreground);
}

.files-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 1rem;
  min-height: 0;
}

@media (max-width: 768px) {
  .files-layout {
    grid-template-columns: 1fr;
  }
}

.file-list {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow-y: auto;
  max-height: calc(100vh - 200px);
}

.file-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.8125rem;
  cursor: pointer;
  border-bottom: 1px solid var(--color-border);
  transition: background 0.15s;
}

.file-item:last-child {
  border-bottom: none;
}

.file-item:hover {
  background: var(--color-accent);
}

.file-item.selected {
  background: var(--color-accent);
  border-left: 2px solid var(--color-primary);
}

.file-icon {
  flex-shrink: 0;
  width: 18px;
  text-align: center;
  font-size: 0.875rem;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  font-size: 0.6875rem;
  color: var(--color-muted-foreground);
  font-family: var(--font-mono);
}

.file-preview {
  overflow-y: auto;
  max-height: calc(100vh - 200px);
}

.preview-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.preview-header code {
  font-size: 0.75rem;
  background: var(--color-muted);
  padding: 0.125rem 0.5rem;
  border-radius: var(--radius-sm);
}

.preview-content {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 1rem;
  font-size: 0.8125rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
  margin: 0;
  font-family: var(--font-mono);
}
</style>
