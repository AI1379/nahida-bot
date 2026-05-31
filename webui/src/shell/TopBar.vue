<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";
import { computed } from "vue";
import { Wifi, WifiOff, LogOut, Radio } from "lucide-vue-next";
import { useAuthStore } from "@/stores/auth";
import { useBootstrap } from "@/api/queries";
import { api } from "@/api/client";
import { connected, disconnectEventStream } from "@/api/events";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const { data: bootstrap } = useBootstrap();

const pageTitle = computed(() => {
  const matched = route.matched[route.matched.length - 1];
  return (matched?.meta?.label as string) ?? "Nahida Bot";
});

async function logout() {
  if (bootstrap.value?.auth.mode === "password") {
    try {
      await api.post("/auth/logout");
    } catch {
      /* local auth state is cleared below */
    }
  }
  auth.clear();
  disconnectEventStream();
  if (bootstrap.value?.auth.required) {
    router.push({ path: "/login", query: { redirect: route.fullPath } });
  }
}
</script>

<template>
  <header class="topbar">
    <h1 class="topbar-title">{{ pageTitle }}</h1>
    <div class="topbar-right">
      <span v-if="bootstrap" class="topbar-version">
        {{ bootstrap.version }}
      </span>
      <span
        class="topbar-sse"
        :class="connected ? 'on' : 'off'"
        :title="connected ? 'Live updates connected' : 'Live updates disconnected'"
      >
        <Radio :size="12" />
      </span>
      <span class="topbar-status" :class="auth.authenticated ? 'ok' : 'off'">
        <Wifi v-if="auth.authenticated" :size="14" />
        <WifiOff v-else :size="14" />
      </span>
      <button
        v-if="auth.authenticated"
        class="topbar-logout"
        title="Logout"
        @click="logout"
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

.topbar-sse {
  display: flex;
  align-items: center;
  transition: color 0.3s;
}

.topbar-sse.on {
  color: var(--color-success);
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--color-success) 34%, transparent));
}

.topbar-sse.off {
  color: var(--color-muted-foreground);
}

.topbar-status {
  display: flex;
  align-items: center;
}

.topbar-status.ok {
  color: var(--color-success);
  filter: drop-shadow(0 0 6px color-mix(in srgb, var(--color-success) 30%, transparent));
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
