<script setup lang="ts">
import { RouterView } from "vue-router";
import NavRail from "./NavRail.vue";
import TopBar from "./TopBar.vue";
import ToastContainer from "@/components/ui/ToastContainer.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import { useAppStore } from "@/stores/app";

const app = useAppStore();
</script>

<template>
  <div class="app-shell">
    <NavRail />
    <div class="main-area">
      <Alert v-if="app.restartRequired" variant="warning" class="restart-banner">
        <span>Configuration saved. A restart is required for changes to take effect.</span>
        <Button variant="destructive" size="sm" @click="$router.push('/')">
          Restart from Overview
        </Button>
        <button class="dismiss-link" @click="app.dismissRestartRequired()">Dismiss</button>
      </Alert>
      <TopBar />
      <main class="content">
        <RouterView />
      </main>
    </div>
  </div>
  <ToastContainer />
</template>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: 1.5rem;
}

.restart-banner {
  margin: 0;
  border-radius: 0;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.5rem 1rem;
  font-size: 0.8125rem;
}

.dismiss-link {
  background: none;
  border: none;
  color: var(--color-muted-foreground);
  cursor: pointer;
  font-size: 0.75rem;
  text-decoration: underline;
  padding: 0;
}

.dismiss-link:hover {
  color: var(--color-foreground);
}
</style>
