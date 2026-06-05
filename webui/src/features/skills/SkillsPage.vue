<script setup lang="ts">
import { computed, ref } from "vue";
import hljs from "highlight.js/lib/core";
import markdown from "highlight.js/lib/languages/markdown";
import yaml from "highlight.js/lib/languages/yaml";
import { useSkills, useSkillContent } from "@/api/queries";
import type { SkillInfo } from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import Spinner from "@/components/ui/Spinner.vue";
import { Brain, FileText } from "lucide-vue-next";

hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("yaml", yaml);

const selectedName = ref<string | null>(null);
const { data, error, isLoading } = useSkills();
const {
  data: detailData,
  isLoading: contentLoading,
} = useSkillContent(selectedName);

const skills = computed<SkillInfo[]>(() => data.value?.skills ?? []);
const selectedSkill = computed<SkillInfo | undefined>(() =>
  skills.value.find((s) => s.name === selectedName.value),
);

function selectSkill(name: string) {
  selectedName.value = name;
}

function renderedContent(raw: string): string {
  if (!raw) return "";
  // Strip YAML frontmatter for display purposes (it's already rendered in metadata)
  const body = raw.replace(/^---[\s\S]*?---\n*/, "");
  return hljs.highlight(body.trim() || raw, { language: "markdown" }).value;
}
</script>

<template>
  <div class="skills-page">
    <Alert v-if="error" variant="destructive">
      Failed to load skills: {{ error.message }}
    </Alert>

    <div v-if="isLoading" class="loading-state">
      <Spinner />
      <span>Loading skills...</span>
    </div>

    <template v-if="data">
      <div v-if="skills.length === 0" class="empty-state">
        <FileText :size="48" class="empty-icon" />
        <p>No skills installed.</p>
        <p class="empty-hint">
          Add skills by creating <code>skills/&lt;name&gt;/SKILL.md</code> files
          in the active workspace.
        </p>
      </div>

      <div v-else class="skills-workspace">
        <!-- Sidebar: skill list -->
        <aside class="skills-sidebar">
          <div class="sidebar-header">
            <h2 class="sidebar-title">Installed Skills</h2>
            <Badge variant="secondary">{{ skills.length }}</Badge>
          </div>
          <div class="skill-list">
            <button
              v-for="skill in skills"
              :key="skill.name"
              class="skill-item"
              :class="{ active: selectedName === skill.name }"
              @click="selectSkill(skill.name)"
            >
              <div class="skill-item-header">
                <Brain :size="16" class="skill-icon" />
                <span class="skill-name">{{ skill.name }}</span>
              </div>
              <p v-if="skill.description" class="skill-desc">
                {{ skill.description }}
              </p>
            </button>
          </div>
        </aside>

        <!-- Main: skill detail -->
        <main class="skills-detail">
          <template v-if="!selectedSkill">
            <div class="detail-empty">
              <Brain :size="40" class="empty-icon" />
              <p>Select a skill from the sidebar to view its instructions.</p>
            </div>
          </template>
          <template v-else>
            <div class="detail-header">
              <h1 class="detail-name">{{ selectedSkill.name }}</h1>
              <Badge variant="secondary">{{ selectedSkill.file_path }}</Badge>
            </div>
            <div v-if="selectedSkill.description" class="detail-description">
              {{ selectedSkill.description }}
            </div>

            <div v-if="contentLoading" class="content-loading">
              <Spinner />
            </div>
            <div v-else-if="detailData" class="detail-content">
              <pre
                class="skill-content"
                v-html="renderedContent(detailData.content)"
              ></pre>
            </div>
          </template>
        </main>
      </div>
    </template>
  </div>
</template>

<style scoped>
.skills-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 0;
}

.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 4rem 2rem;
  color: var(--color-muted-foreground);
}

.empty-icon {
  opacity: 0.4;
}

.empty-hint {
  font-size: 0.85rem;
  color: var(--color-muted-foreground);
}

.empty-hint code {
  background: var(--color-muted);
  padding: 0.125rem 0.375rem;
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
}

.skills-workspace {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* Sidebar */
.skills-sidebar {
  width: 320px;
  flex-shrink: 0;
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  background: var(--color-sidebar);
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--color-border);
}

.sidebar-title {
  font-size: 0.95rem;
  font-weight: 600;
  margin: 0;
}

.skill-list {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.skill-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s;
  color: var(--color-foreground);
}

.skill-item:hover {
  background: var(--color-accent);
}

.skill-item.active {
  background: color-mix(in srgb, var(--color-primary) 10%, transparent);
  box-shadow: inset 2px 0 0 var(--color-primary);
}

.skill-item-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.skill-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}

.skill-name {
  font-weight: 600;
  font-size: 0.9rem;
}

.skill-desc {
  margin: 0;
  font-size: 0.8rem;
  color: var(--color-muted-foreground);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Detail */
.skills-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.detail-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  padding: 4rem 2rem;
  color: var(--color-muted-foreground);
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.25rem 1.5rem 0.5rem;
  border-bottom: 1px solid var(--color-border);
}

.detail-name {
  font-size: 1.15rem;
  font-weight: 700;
  margin: 0;
}

.detail-description {
  padding: 0.75rem 1.5rem;
  font-size: 0.9rem;
  color: var(--color-muted-foreground);
  border-bottom: 1px solid var(--color-border);
  line-height: 1.5;
}

.content-loading {
  padding: 2rem;
  display: flex;
  justify-content: center;
}

.detail-content {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
}

.skill-content {
  margin: 0;
  padding: 1.25rem;
  background: var(--color-muted);
  border-radius: var(--radius-md);
  font-family: var(--font-mono, "Cascadia Code", "Fira Code", monospace);
  font-size: 0.85rem;
  line-height: 1.65;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

/* Highlight.js theme overrides — use design tokens */
:deep(.skill-content .hljs-keyword) { color: var(--color-primary); }
:deep(.skill-content .hljs-string) { color: #6abf69; }
:deep(.skill-content .hljs-comment) { color: var(--color-muted-foreground); font-style: italic; }
:deep(.skill-content .hljs-title) { color: #dcdcaa; }
:deep(.skill-content .hljs-section) { color: var(--color-primary); font-weight: 600; }
:deep(.skill-content .hljs-attr) { color: #9cdcfe; }
:deep(.skill-content .hljs-bullet) { color: var(--color-primary); }
:deep(.skill-content .hljs-code) { color: #ce9178; }
</style>
