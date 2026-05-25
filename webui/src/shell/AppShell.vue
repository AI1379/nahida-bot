<script setup lang="ts">
import { RouterView } from "vue-router";
import NavRail from "./NavRail.vue";
import TopBar from "./TopBar.vue";
</script>

<template>
  <div class="app-shell">
    <NavRail />
    <div class="main-area">
      <TopBar />
      <main class="content">
        <RouterView />
      </main>
    </div>
  </div>
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
</style>
