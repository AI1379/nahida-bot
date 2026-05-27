<script setup lang="ts">
import { ref, computed } from "vue";
import { useWorkspaces, useFileList, useFileContent, useFileSave, useFileCreate, useFileRename, useFileDelete } from "@/api/queries";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Input from "@/components/ui/Input.vue";
import Textarea from "@/components/ui/Textarea.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import Spinner from "@/components/ui/Spinner.vue";
import { Plus, Pencil, Trash2 } from "lucide-vue-next";

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

// Mutations
const saveMutation = useFileSave();
const createMutation = useFileCreate();
const renameMutation = useFileRename();
const deleteMutation = useFileDelete();

// Create file dialog
const showCreateDialog = ref(false);
const newFileName = ref("");
const newFileContent = ref("");

// Rename dialog
const showRenameDialog = ref(false);
const renamePath = ref("");
const renameOldName = ref("");
const renameNewName = ref("");

// Delete confirm
const showDeleteDialog = ref(false);
const deletePath = ref("");
const deleteName = ref("");

// Inline editing
const isEditing = ref(false);
const editContent = ref("");

const pathParts = computed(() => {
  if (currentPath.value === ".") return [];
  return currentPath.value.split("/").filter(Boolean);
});

function navigateUp() {
  const parts = currentPath.value.split("/").filter(Boolean);
  parts.pop();
  currentPath.value = parts.length ? parts.join("/") : ".";
  selectedFile.value = "";
  isEditing.value = false;
}

function navigateTo(index: number) {
  const parts = currentPath.value.split("/").filter(Boolean);
  currentPath.value = parts.slice(0, index + 1).join("/") || ".";
  selectedFile.value = "";
  isEditing.value = false;
}

function enterDir(name: string) {
  currentPath.value =
    currentPath.value === "." ? name : `${currentPath.value}/${name}`;
  selectedFile.value = "";
  isEditing.value = false;
}

function selectFile(name: string) {
  selectedFile.value =
    currentPath.value === "."
      ? name
      : `${currentPath.value}/${name}`;
  isEditing.value = false;
}

function isTextFile(name: string) {
  return /\.(md|txt|yaml|yml|json)$/i.test(name);
}

const isMarkdown = computed(() =>
  /\.md$/i.test(selectedFile.value),
);

// Create file
function openCreateDialog() {
  newFileName.value = "";
  newFileContent.value = "";
  showCreateDialog.value = true;
}

function handleCreate() {
  if (!newFileName.value.trim()) return;
  const dir = currentPath.value === "." ? "" : currentPath.value + "/";
  const fullPath = dir + newFileName.value;
  createMutation.mutate(
    {
      path: fullPath,
      content: newFileContent.value,
      workspace_id: workspaceId.value,
    },
    {
      onSuccess: () => {
        showCreateDialog.value = false;
        // Select the new file if it's a text file
        if (isTextFile(newFileName.value)) {
          selectedFile.value = fullPath;
        }
      },
    },
  );
}

// Rename
function openRename(name: string) {
  const dir = currentPath.value === "." ? "" : currentPath.value + "/";
  renamePath.value = dir + name;
  renameOldName.value = name;
  renameNewName.value = name;
  showRenameDialog.value = true;
}

function handleRename() {
  if (!renameNewName.value.trim()) return;
  renameMutation.mutate(
    {
      path: renamePath.value,
      new_name: renameNewName.value,
      workspace_id: workspaceId.value,
    },
    {
      onSuccess: () => {
        showRenameDialog.value = false;
        if (selectedFile.value === renamePath.value) {
          const dir = currentPath.value === "." ? "" : currentPath.value + "/";
          selectedFile.value = dir + renameNewName.value;
        }
      },
    },
  );
}

// Delete
function openDelete(name: string) {
  const dir = currentPath.value === "." ? "" : currentPath.value + "/";
  deletePath.value = dir + name;
  deleteName.value = name;
  showDeleteDialog.value = true;
}

function handleDelete() {
  deleteMutation.mutate(
    {
      path: deletePath.value,
      workspace_id: workspaceId.value,
    },
    {
      onSuccess: () => {
        showDeleteDialog.value = false;
        if (selectedFile.value === deletePath.value) {
          selectedFile.value = "";
          isEditing.value = false;
        }
      },
    },
  );
}

// Inline editing
function startEditing() {
  if (!contentQuery.data.value) return;
  editContent.value = contentQuery.data.value.content;
  isEditing.value = true;
}

function cancelEditing() {
  isEditing.value = false;
  editContent.value = "";
}

function saveFile() {
  saveMutation.mutate(
    {
      path: selectedFile.value,
      content: editContent.value,
      workspace_id: workspaceId.value,
    },
    {
      onSuccess: () => {
        isEditing.value = false;
      },
    },
  );
}
</script>

<template>
  <div class="files-page">
    <Alert v-if="listError" variant="destructive">
      Failed to load files: {{ listError.message }}
    </Alert>

    <div v-if="wsLoading || listLoading" class="loading">Loading...</div>

    <template v-if="wsData">
      <!-- Workspace selector + actions -->
      <div class="files-toolbar">
        <span class="toolbar-label">Workspace: <strong>{{ wsData.active }}</strong></span>
        <Badge variant="outline">{{ workspaceId }}</Badge>
        <Button size="sm" @click="openCreateDialog">
          <Plus :size="14" />
          New File
        </Button>
      </div>

      <!-- Breadcrumb -->
      <div class="breadcrumb">
        <button class="crumb" @click="currentPath = '.'; selectedFile = ''; isEditing = false">root</button>
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
            <div v-if="!entry.is_dir" class="file-actions">
              <button class="action-btn" title="Rename" @click.stop="openRename(entry.name)">
                <Pencil :size="12" />
              </button>
              <button class="action-btn action-btn-danger" title="Delete" @click.stop="openDelete(entry.name)">
                <Trash2 :size="12" />
              </button>
            </div>
          </div>
          <div v-if="fileList && !fileList.entries.length" class="empty">Empty directory.</div>
        </div>

        <!-- Preview / Editor -->
        <div class="file-preview">
          <div v-if="!selectedFile" class="empty">Select a text file to preview.</div>
          <div v-else-if="contentQuery.isLoading.value" class="loading">Loading...</div>
          <template v-else-if="contentQuery.data.value">
            <div class="preview-header">
              <code>{{ selectedFile }}</code>
              <Badge variant="outline">{{ contentQuery.data.value.size }} B</Badge>
              <div class="preview-actions">
                <template v-if="!isEditing">
                  <Button size="sm" @click="startEditing">Edit</Button>
                </template>
                <template v-else>
                  <Button size="sm" :disabled="saveMutation.isPending.value" @click="saveFile">
                    <Spinner v-if="saveMutation.isPending.value" size="sm" />
                    Save
                  </Button>
                  <Button size="sm" variant="outline" :disabled="saveMutation.isPending.value" @click="cancelEditing">
                    Discard
                  </Button>
                </template>
              </div>
            </div>
            <div v-if="!isEditing" class="preview-content" :class="{ markdown: isMarkdown }">{{ contentQuery.data.value.content }}</div>
            <Textarea
              v-else
              v-model="editContent"
              :rows="30"
              class="file-editor"
            />
          </template>
        </div>
      </div>
    </template>

    <!-- Create file dialog -->
    <ConfirmDialog
      v-model:open="showCreateDialog"
      title="New File"
      confirm-label="Create"
      :loading="createMutation.isPending.value"
      :disabled="!newFileName.trim()"
      @confirm="handleCreate"
    >
      <div class="create-form">
        <Input v-model="newFileName" placeholder="filename.md" class="create-input" />
        <Textarea v-model="newFileContent" placeholder="Initial content (optional)" :rows="5" />
      </div>
    </ConfirmDialog>

    <!-- Rename dialog -->
    <ConfirmDialog
      v-model:open="showRenameDialog"
      title="Rename"
      :description="`Rename ${renameOldName} to:`"
      confirm-label="Rename"
      :loading="renameMutation.isPending.value"
      :disabled="!renameNewName.trim()"
      @confirm="handleRename"
    >
      <Input v-model="renameNewName" class="rename-input" />
    </ConfirmDialog>

    <!-- Delete confirm -->
    <ConfirmDialog
      v-model:open="showDeleteDialog"
      title="Delete File"
      :description="`Delete ${deleteName}? The file will be moved to .trash.`"
      variant="destructive"
      confirm-label="Delete"
      :loading="deleteMutation.isPending.value"
      @confirm="handleDelete"
    />
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

.file-actions {
  display: none;
  gap: 0.125rem;
  flex-shrink: 0;
}

.file-item:hover .file-actions {
  display: flex;
}

.action-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-muted-foreground);
  padding: 0.25rem;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.15s, background 0.15s;
}

.action-btn:hover {
  color: var(--color-foreground);
  background: var(--color-muted);
}

.action-btn-danger:hover {
  color: var(--color-destructive);
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

.preview-actions {
  margin-left: auto;
  display: flex;
  gap: 0.375rem;
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

.file-editor {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
  resize: vertical;
  min-height: 400px;
}

.create-form {
  margin-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.create-input {
  font-family: var(--font-mono);
}

.rename-input {
  margin-top: 0.5rem;
}
</style>
