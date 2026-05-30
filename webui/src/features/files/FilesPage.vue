<script setup lang="ts">
import { computed, ref } from "vue";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import lua from "highlight.js/lib/languages/lua";
import markdown from "highlight.js/lib/languages/markdown";
import perl from "highlight.js/lib/languages/perl";
import php from "highlight.js/lib/languages/php";
import powershell from "highlight.js/lib/languages/powershell";
import python from "highlight.js/lib/languages/python";
import r from "highlight.js/lib/languages/r";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import scss from "highlight.js/lib/languages/scss";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import {
  useFileContent,
  useFileCreate,
  useFileDelete,
  useFileList,
  useFileRename,
  useFileSave,
  useFileUpload,
  useWorkspaces,
} from "@/api/queries";
import type { FileEntry } from "@/api/schemas";
import { useAuthStore } from "@/stores/auth";
import { formatBytes } from "@/lib/utils";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Input from "@/components/ui/Input.vue";
import Textarea from "@/components/ui/Textarea.vue";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import Spinner from "@/components/ui/Spinner.vue";
import {
  FileText,
  Folder,
  Image as ImageIcon,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from "lucide-vue-next";

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("csharp", csharp);
hljs.registerLanguage("css", css);
hljs.registerLanguage("go", go);
hljs.registerLanguage("ini", ini);
hljs.registerLanguage("java", java);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("kotlin", kotlin);
hljs.registerLanguage("lua", lua);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("perl", perl);
hljs.registerLanguage("php", php);
hljs.registerLanguage("powershell", powershell);
hljs.registerLanguage("python", python);
hljs.registerLanguage("r", r);
hljs.registerLanguage("ruby", ruby);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("scss", scss);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("swift", swift);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("yaml", yaml);

const TEXT_FILE_EXTENSIONS = new Set([
  ".bash",
  ".c",
  ".cc",
  ".cfg",
  ".cjs",
  ".conf",
  ".cpp",
  ".cs",
  ".css",
  ".cxx",
  ".fish",
  ".go",
  ".h",
  ".hh",
  ".hpp",
  ".html",
  ".htm",
  ".hxx",
  ".ini",
  ".java",
  ".js",
  ".json",
  ".jsx",
  ".kt",
  ".kts",
  ".lua",
  ".mjs",
  ".md",
  ".php",
  ".pl",
  ".ps1",
  ".py",
  ".pyw",
  ".r",
  ".rb",
  ".rs",
  ".sass",
  ".scss",
  ".sh",
  ".sql",
  ".swift",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".vue",
  ".xml",
  ".yaml",
  ".yml",
  ".zsh",
]);

const TEXT_FILE_NAMES = new Set([
  "dockerfile",
  "gemfile",
  "justfile",
  "makefile",
  "rakefile",
]);

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  ".bash": "bash",
  ".c": "cpp",
  ".cc": "cpp",
  ".cfg": "ini",
  ".cjs": "javascript",
  ".conf": "ini",
  ".cpp": "cpp",
  ".cs": "csharp",
  ".css": "css",
  ".cxx": "cpp",
  ".fish": "bash",
  ".go": "go",
  ".h": "cpp",
  ".hh": "cpp",
  ".hpp": "cpp",
  ".html": "xml",
  ".htm": "xml",
  ".hxx": "cpp",
  ".ini": "ini",
  ".java": "java",
  ".js": "javascript",
  ".json": "json",
  ".jsx": "javascript",
  ".kt": "kotlin",
  ".kts": "kotlin",
  ".lua": "lua",
  ".mjs": "javascript",
  ".md": "markdown",
  ".php": "php",
  ".pl": "perl",
  ".ps1": "powershell",
  ".py": "python",
  ".pyw": "python",
  ".r": "r",
  ".rb": "ruby",
  ".rs": "rust",
  ".sass": "scss",
  ".scss": "scss",
  ".sh": "bash",
  ".sql": "sql",
  ".swift": "swift",
  ".toml": "ini",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".vue": "xml",
  ".xml": "xml",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".zsh": "bash",
};

const LANGUAGE_BY_FILENAME: Record<string, string> = {
  dockerfile: "bash",
  gemfile: "ruby",
  justfile: "bash",
  makefile: "bash",
  rakefile: "ruby",
};

const auth = useAuthStore();
const { data: wsData, isLoading: wsLoading } = useWorkspaces();

const currentPath = ref(".");
const selectedFile = ref("");
const workspaceId = computed(() => wsData.value?.active ?? "default");

const {
  data: fileList,
  isLoading: listLoading,
  error: listError,
} = useFileList(workspaceId, currentPath);

const selectedTextFile = computed(() =>
  isTextFile(selectedFile.value) ? selectedFile.value : "",
);
const contentQuery = useFileContent(workspaceId, selectedTextFile);

const saveMutation = useFileSave();
const createMutation = useFileCreate();
const uploadMutation = useFileUpload();
const renameMutation = useFileRename();
const deleteMutation = useFileDelete();

const showCreateDialog = ref(false);
const newFileName = ref("");
const newFileContent = ref("");

const showUploadDialog = ref(false);
const uploadFile = ref<File | null>(null);
const uploadPath = ref("");
const uploadOverwrite = ref(false);

const showRenameDialog = ref(false);
const renamePath = ref("");
const renameOldName = ref("");
const renameNewName = ref("");

const showDeleteDialog = ref(false);
const deletePath = ref("");
const deleteName = ref("");

const isEditing = ref(false);
const editContent = ref("");

const pathParts = computed(() => {
  if (currentPath.value === ".") return [];
  return currentPath.value.split("/").filter(Boolean);
});

const isSelectedTextFile = computed(() => isTextFile(selectedFile.value));
const isSelectedImageFile = computed(() => isImageFile(selectedFile.value));

const selectedEntry = computed<FileEntry | null>(() => {
  if (!selectedFile.value || !fileList.value) return null;
  return (
    fileList.value.entries.find((entry) => entryFullPath(entry) === selectedFile.value) ??
    null
  );
});

const imagePreviewUrl = computed(() => {
  if (!isSelectedImageFile.value) return "";
  const params = new URLSearchParams({
    workspace_id: workspaceId.value,
    path: selectedFile.value,
  });
  if (selectedEntry.value?.mtime) params.set("v", selectedEntry.value.mtime);
  if (auth.token) params.set("token", auth.token);
  return `/api/files/raw?${params.toString()}`;
});

const highlightedPreview = computed(() => {
  const content = contentQuery.data.value?.content ?? "";
  const language = languageForFile(selectedFile.value);
  if (!content || !language) {
    return { language: "plaintext", html: escapeHtml(content) };
  }
  if (language === "plaintext" || !hljs.getLanguage(language)) {
    return { language: "plaintext", html: escapeHtml(content) };
  }
  return {
    language,
    html: hljs.highlight(content, { language, ignoreIllegals: true }).value,
  };
});

function resetSelection() {
  selectedFile.value = "";
  isEditing.value = false;
}

function navigateUp() {
  const parts = currentPath.value.split("/").filter(Boolean);
  parts.pop();
  currentPath.value = parts.length ? parts.join("/") : ".";
  resetSelection();
}

function navigateTo(index: number) {
  const parts = currentPath.value.split("/").filter(Boolean);
  currentPath.value = parts.slice(0, index + 1).join("/") || ".";
  resetSelection();
}

function navigateRoot() {
  currentPath.value = ".";
  resetSelection();
}

function enterDir(name: string) {
  currentPath.value =
    currentPath.value === "." ? name : `${currentPath.value}/${name}`;
  resetSelection();
}

function selectFile(name: string) {
  selectedFile.value =
    currentPath.value === "."
      ? name
      : `${currentPath.value}/${name}`;
  isEditing.value = false;
}

function entryFullPath(entry: FileEntry) {
  return currentPath.value === "." ? entry.name : `${currentPath.value}/${entry.name}`;
}

function isTextFile(name: string) {
  const basename = fileBasename(name).toLowerCase();
  return TEXT_FILE_NAMES.has(basename) || TEXT_FILE_EXTENSIONS.has(fileExtension(name));
}

function isImageFile(name: string) {
  return /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(name);
}

function languageForFile(name: string) {
  const basename = fileBasename(name).toLowerCase();
  return LANGUAGE_BY_FILENAME[basename] ?? LANGUAGE_BY_EXTENSION[fileExtension(name)] ?? "plaintext";
}

function fileBasename(path: string) {
  return path.replace(/\\/g, "/").split("/").pop() ?? "";
}

function fileExtension(path: string) {
  const basename = fileBasename(path).toLowerCase();
  const index = basename.lastIndexOf(".");
  return index > 0 ? basename.slice(index) : "";
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function openCreateDialog() {
  newFileName.value = "";
  newFileContent.value = "";
  showCreateDialog.value = true;
}

function handleCreate() {
  if (!newFileName.value.trim()) return;
  const dir = currentPath.value === "." ? "" : currentPath.value + "/";
  const fullPath = dir + newFileName.value.trim();
  createMutation.mutate(
    {
      path: fullPath,
      content: newFileContent.value,
      workspace_id: workspaceId.value,
    },
    {
      onSuccess: () => {
        showCreateDialog.value = false;
        selectedFile.value = fullPath;
      },
    },
  );
}

function openUploadDialog() {
  uploadFile.value = null;
  uploadPath.value = currentPath.value === "." ? "" : currentPath.value + "/";
  uploadOverwrite.value = false;
  showUploadDialog.value = true;
}

function handleUploadFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  uploadFile.value = file;
  if (file) {
    const dir = currentPath.value === "." ? "" : currentPath.value + "/";
    uploadPath.value = dir + file.name;
  }
}

function handleUpload() {
  if (!uploadFile.value || !uploadPath.value.trim()) return;
  uploadMutation.mutate(
    {
      path: uploadPath.value.trim(),
      file: uploadFile.value,
      workspace_id: workspaceId.value,
      overwrite: uploadOverwrite.value,
    },
    {
      onSuccess: (data) => {
        showUploadDialog.value = false;
        selectedFile.value = data.path.replace(/\\/g, "/");
        isEditing.value = false;
      },
    },
  );
}

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
      new_name: renameNewName.value.trim(),
      workspace_id: workspaceId.value,
    },
    {
      onSuccess: () => {
        showRenameDialog.value = false;
        if (selectedFile.value === renamePath.value) {
          const dir = currentPath.value === "." ? "" : currentPath.value + "/";
          selectedFile.value = dir + renameNewName.value.trim();
        }
      },
    },
  );
}

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
          resetSelection();
        }
      },
    },
  );
}

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
      <div class="files-toolbar">
        <span class="toolbar-label">Workspace: <strong>{{ workspaceId }}</strong></span>
        <Badge variant="outline">{{ workspaceId }}</Badge>
        <div class="toolbar-actions">
          <Button size="sm" variant="outline" @click="openUploadDialog">
            <Upload :size="14" />
            Upload
          </Button>
          <Button size="sm" @click="openCreateDialog">
            <Plus :size="14" />
            New File
          </Button>
        </div>
      </div>

      <div class="breadcrumb">
        <button class="crumb" @click="navigateRoot">root</button>
        <template v-for="(part, i) in pathParts" :key="i">
          <span class="crumb-sep">/</span>
          <button class="crumb" @click="navigateTo(i)">{{ part }}</button>
        </template>
      </div>

      <div class="files-layout">
        <div class="file-list">
          <div v-if="currentPath !== '.'" class="file-item dir-item" @click="navigateUp">
            <span class="file-icon"><Folder :size="16" /></span>
            <span class="file-name">..</span>
          </div>
          <div
            v-for="entry in fileList?.entries"
            :key="entry.path"
            class="file-item"
            :class="{ selected: selectedFile === entryFullPath(entry) }"
            @click="entry.is_dir ? enterDir(entry.name) : selectFile(entry.name)"
          >
            <span class="file-icon">
              <Folder v-if="entry.is_dir" :size="16" />
              <ImageIcon v-else-if="isImageFile(entry.name)" :size="16" />
              <FileText v-else :size="16" />
            </span>
            <span class="file-name">{{ entry.name }}</span>
            <span v-if="!entry.is_dir" class="file-size">{{ formatBytes(entry.size) }}</span>
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

        <div class="file-preview">
          <div v-if="!selectedFile" class="empty">Select a file to preview.</div>
          <template v-else>
            <div class="preview-header">
              <code>{{ selectedFile }}</code>
              <Badge v-if="selectedEntry" variant="outline">{{ formatBytes(selectedEntry.size) }}</Badge>
              <Badge v-if="isSelectedImageFile" variant="secondary">Image</Badge>
              <Badge v-else-if="isSelectedTextFile" variant="secondary">Text</Badge>
              <div v-if="isSelectedTextFile" class="preview-actions">
                <template v-if="!isEditing">
                  <Button size="sm" :disabled="!contentQuery.data.value" @click="startEditing">Edit</Button>
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

            <div v-if="isSelectedImageFile" class="image-preview-shell">
              <img class="image-preview" :src="imagePreviewUrl" :alt="selectedFile" />
            </div>

            <template v-else-if="isSelectedTextFile">
              <div v-if="contentQuery.isLoading.value" class="loading">Loading...</div>
              <Alert v-else-if="contentQuery.error.value" variant="destructive">
                Failed to load file: {{ contentQuery.error.value.message }}
              </Alert>
              <template v-else-if="contentQuery.data.value">
                <pre
                  v-if="!isEditing"
                  class="preview-content code-preview"
                ><code
                  class="hljs"
                  :class="`language-${highlightedPreview.language}`"
                  v-html="highlightedPreview.html"
                /></pre>
                <Textarea
                  v-else
                  v-model="editContent"
                  :rows="30"
                  class="file-editor"
                />
              </template>
            </template>

            <div v-else class="empty">Preview unavailable for this file type.</div>
          </template>
        </div>
      </div>
    </template>

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

    <ConfirmDialog
      v-model:open="showUploadDialog"
      title="Upload File"
      confirm-label="Upload"
      :loading="uploadMutation.isPending.value"
      :disabled="!uploadFile || !uploadPath.trim()"
      @confirm="handleUpload"
    >
      <div class="upload-form">
        <label class="form-field">
          <span>File</span>
          <input class="file-input" type="file" @change="handleUploadFileChange" />
        </label>
        <label class="form-field">
          <span>Target path</span>
          <Input v-model="uploadPath" placeholder="images/picture.png" class="create-input" />
        </label>
        <label class="checkbox-row">
          <input v-model="uploadOverwrite" type="checkbox" />
          <span>Overwrite existing file</span>
        </label>
        <div v-if="uploadFile" class="upload-summary">
          {{ uploadFile.name }} - {{ formatBytes(uploadFile.size) }}
        </div>
      </div>
    </ConfirmDialog>

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
  flex-wrap: wrap;
}

.toolbar-label {
  color: var(--color-muted-foreground);
}

.toolbar-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.8125rem;
  flex-wrap: wrap;
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

  .toolbar-actions {
    margin-left: 0;
    width: 100%;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-muted-foreground);
}

.file-name {
  flex: 1;
  min-width: 0;
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
  flex-wrap: wrap;
}

.preview-header code {
  font-size: 0.75rem;
  background: var(--color-muted);
  padding: 0.125rem 0.5rem;
  border-radius: var(--radius-sm);
  word-break: break-all;
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
  overflow-x: auto;
  margin: 0;
  font-family: var(--font-mono);
}

.code-preview {
  color: var(--color-foreground);
  white-space: pre;
  word-break: normal;
}

.code-preview code {
  display: block;
  min-width: max-content;
  background: transparent;
  color: inherit;
  font-family: inherit;
}

.code-preview :deep(.hljs-comment),
.code-preview :deep(.hljs-quote) {
  color: color-mix(in srgb, var(--color-muted-foreground) 80%, transparent);
  font-style: italic;
}

.code-preview :deep(.hljs-keyword),
.code-preview :deep(.hljs-selector-tag),
.code-preview :deep(.hljs-subst) {
  color: #c678dd;
}

.code-preview :deep(.hljs-string),
.code-preview :deep(.hljs-regexp),
.code-preview :deep(.hljs-symbol),
.code-preview :deep(.hljs-bullet) {
  color: #50a14f;
}

.code-preview :deep(.hljs-number),
.code-preview :deep(.hljs-literal),
.code-preview :deep(.hljs-variable),
.code-preview :deep(.hljs-template-variable) {
  color: #0184bc;
}

.code-preview :deep(.hljs-title),
.code-preview :deep(.hljs-section),
.code-preview :deep(.hljs-name),
.code-preview :deep(.hljs-function) {
  color: #4078f2;
}

.code-preview :deep(.hljs-type),
.code-preview :deep(.hljs-class .hljs-title),
.code-preview :deep(.hljs-built_in),
.code-preview :deep(.hljs-builtin-name) {
  color: #c18401;
}

.code-preview :deep(.hljs-attr),
.code-preview :deep(.hljs-attribute),
.code-preview :deep(.hljs-meta),
.code-preview :deep(.hljs-selector-id),
.code-preview :deep(.hljs-selector-class) {
  color: #e45649;
}

.code-preview :deep(.hljs-emphasis) {
  font-style: italic;
}

.code-preview :deep(.hljs-strong) {
  font-weight: 600;
}

.image-preview-shell {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background:
    linear-gradient(45deg, var(--color-muted) 25%, transparent 25%),
    linear-gradient(-45deg, var(--color-muted) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, var(--color-muted) 75%),
    linear-gradient(-45deg, transparent 75%, var(--color-muted) 75%);
  background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  background-size: 16px 16px;
  padding: 1rem;
  min-height: 240px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.image-preview {
  display: block;
  max-width: 100%;
  max-height: calc(100vh - 280px);
  object-fit: contain;
}

.file-editor {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
  resize: vertical;
  min-height: 400px;
}

.create-form,
.upload-form {
  margin-top: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.create-input {
  font-family: var(--font-mono);
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.file-input {
  width: 100%;
  font-size: 0.8125rem;
  color: var(--color-foreground);
}

.checkbox-row {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
}

.upload-summary {
  color: var(--color-muted-foreground);
  font-family: var(--font-mono);
  font-size: 0.75rem;
}

.rename-input {
  margin-top: 0.5rem;
}
</style>
