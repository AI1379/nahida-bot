<script setup lang="ts">
import { useRoute, RouterLink } from "vue-router";
import {
  LayoutDashboard,
  Settings,
  Clock,
  MessageSquare,
  FolderOpen,
  ScrollText,
} from "lucide-vue-next";
import { computed, type Component } from "vue";

const route = useRoute();

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
];

const activeName = computed(() => {
  const matched = route.matched[route.matched.length - 1];
  return matched?.name as string | undefined;
});
</script>

<template>
  <nav class="nav-rail">
    <div class="nav-logo">
      <span class="logo-icon">🌿</span>
    </div>
    <div class="nav-items">
      <RouterLink
        v-for="item in items"
        :key="item.name"
        :to="item.to"
        class="nav-item"
        :class="{ active: activeName === item.name }"
        :title="item.label"
      >
        <component :is="item.icon" :size="20" />
      </RouterLink>
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
}

.nav-logo {
  margin-bottom: 1rem;
  font-size: 1.25rem;
}

.nav-items {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
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
</style>
