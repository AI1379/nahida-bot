<script setup lang="ts">
import { useRoute, RouterLink } from "vue-router";
import {
  LayoutDashboard,
  Settings,
  Clock,
  MessageSquare,
  FolderOpen,
  ScrollText,
  Plug,
  Coins,
  Brain,
  BookOpen,
  Info,
  PanelLeftOpen,
  PanelLeftClose,
} from "lucide-vue-next";
import { computed, ref, type Component } from "vue";
import { usePluginList } from "@/api/queries";

const route = useRoute();
const { data: pluginData } = usePluginList();

interface NavItem {
  name: string;
  icon: Component;
  to: string;
  label: string;
}

const items: NavItem[] = [
  { name: "home", icon: LayoutDashboard, to: "/", label: "Overview" },
  { name: "config", icon: Settings, to: "/config", label: "Config" },
  { name: "cron", icon: Clock, to: "/cron", label: "CRON" },
  { name: "sessions", icon: MessageSquare, to: "/sessions", label: "Sessions" },
  { name: "files", icon: FolderOpen, to: "/files", label: "Files" },
  { name: "logs", icon: ScrollText, to: "/logs", label: "Logs" },
  { name: "plugins", icon: Plug, to: "/plugins", label: "Plugins" },
  { name: "kb", icon: BookOpen, to: "/kb", label: "Knowledge" },
  { name: "skills", icon: Brain, to: "/skills", label: "Skills" },
  { name: "usage", icon: Coins, to: "/usage", label: "Usage" },
  { name: "about", icon: Info, to: "/about", label: "About" },
];

const visibleItems = computed(() => {
  const knowledgeBaseAvailable = pluginData.value?.plugins.some(
    (plugin) =>
      plugin.id === "knowledge_base"
      && plugin.state === "enabled"
      && plugin.has_instance,
  );
  return items.filter((item) => item.name !== "kb" || knowledgeBaseAvailable);
});

const activeName = computed(() => {
  const matched = route.matched[route.matched.length - 1];
  return matched?.name as string | undefined;
});

const expanded = ref(localStorage.getItem("sidebar-expanded") === "true");

function toggleSidebar() {
  expanded.value = !expanded.value;
  localStorage.setItem("sidebar-expanded", String(expanded.value));
}
</script>

<template>
  <nav class="nav-rail" :class="{ expanded }">
    <div class="nav-logo">
      <span class="logo-icon">🌿</span>
      <span v-if="expanded" class="logo-text">Nahida Bot</span>
    </div>
    <div class="nav-items">
      <RouterLink
        v-for="item in visibleItems"
        :key="item.name"
        :to="item.to"
        class="nav-item"
        :class="{ active: activeName === item.name }"
        :title="!expanded ? item.label : undefined"
      >
        <component :is="item.icon" :size="20" class="nav-icon" />
        <span v-if="expanded" class="nav-label">{{ item.label }}</span>
      </RouterLink>
    </div>
    <div class="nav-footer">
      <button
        class="toggle-btn"
        :title="expanded ? 'Collapse sidebar' : 'Expand sidebar'"
        @click="toggleSidebar"
      >
        <component :is="expanded ? PanelLeftClose : PanelLeftOpen" :size="18" />
      </button>
    </div>
  </nav>
</template>

<style scoped>
.nav-rail {
  width: 52px;
  background: var(--color-sidebar);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 0.75rem 0;
  flex-shrink: 0;
  transition: width 0.2s ease;
  overflow: hidden;
}

.nav-rail.expanded {
  width: 180px;
  align-items: stretch;
  padding: 0.75rem 0.5rem;
}

.nav-logo {
  margin-bottom: 1rem;
  font-size: 1.25rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 0.25rem;
}

.nav-rail:not(.expanded) .nav-logo {
  justify-content: center;
}

.logo-text {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-foreground);
  white-space: nowrap;
}

.nav-items {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  flex: 1;
}

.nav-rail.expanded .nav-items {
  padding: 0 0.25rem;
}

.nav-item {
  position: relative;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: var(--color-muted-foreground);
  transition: background 0.15s, box-shadow 0.15s, color 0.15s;
  text-decoration: none;
  flex-shrink: 0;
}

.nav-rail:not(.expanded) .nav-item {
  margin: 0 auto;
}

.nav-rail.expanded .nav-item {
  width: auto;
  justify-content: flex-start;
  padding: 0 0.5rem;
  gap: 0.625rem;
}

.nav-item:hover {
  background: var(--color-accent);
  color: var(--color-foreground);
}

.nav-item.active {
  background: color-mix(in srgb, var(--color-primary) 6%, transparent);
  color: var(--color-primary);
  box-shadow:
    inset 3px 0 0 var(--color-primary),
    0 0 8px color-mix(in srgb, var(--color-primary) 5%, transparent);
}

.nav-icon {
  flex-shrink: 0;
}

.nav-label {
  font-size: 0.8125rem;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-footer {
  display: flex;
  justify-content: center;
  padding-top: 0.5rem;
}

.nav-rail.expanded .nav-footer {
  justify-content: flex-end;
  padding-right: 0.25rem;
}

.toggle-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  border: none;
  background: transparent;
  color: var(--color-muted-foreground);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.toggle-btn:hover {
  background: var(--color-accent);
  color: var(--color-foreground);
}
</style>
