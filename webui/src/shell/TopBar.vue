<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import { Wifi, WifiOff, LogOut } from "lucide-vue-next";
import { useAuthStore } from "@/stores/auth";
import { useBootstrap } from "@/api/queries";

const route = useRoute();
const auth = useAuthStore();
const { data: bootstrap } = useBootstrap();

const pageTitle = computed(() => {
  const matched = route.matched[route.matched.length - 1];
  return (matched?.meta?.label as string) ?? "Nahida Bot";
});
</script>

<template>
  <header class="topbar">
    <h1 class="topbar-title">{{ pageTitle }}</h1>
    <div class="topbar-right">
      <span v-if="bootstrap" class="topbar-version">
        {{ bootstrap.version }}
      </span>
      <span class="topbar-status" :class="auth.authenticated ? 'ok' : 'off'">
        <Wifi v-if="auth.authenticated" :size="14" />
        <WifiOff v-else :size="14" />
      </span>
      <button
        v-if="auth.authenticated"
        class="topbar-logout"
        title="Logout"
        @click="auth.clear()"
      >
        <LogOut :size="14" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1.5rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-card);
  flex-shrink: 0;
}

.topbar-title {
  font-size: 0.875rem;
  font-weight: 600;
  margin: 0;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.topbar-version {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
}

.topbar-status {
  display: flex;
  align-items: center;
}

.topbar-status.ok {
  color: var(--color-success);
}

.topbar-status.off {
  color: var(--color-destructive);
}

.topbar-logout {
  display: flex;
  align-items: center;
  background: none;
  border: none;
  color: var(--color-muted-foreground);
  cursor: pointer;
  padding: 2px;
  border-radius: var(--radius-sm);
}

.topbar-logout:hover {
  color: var(--color-foreground);
  background: var(--color-accent);
}
</style>
