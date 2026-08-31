<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import type { ActiveRemotePluginPage } from "@/plugins/desktopPluginContract";
import {
  fetchDesktopPluginPage,
  sandboxPluginPageDocument,
  type PluginPageDocument,
} from "@/services/pluginPageService";

const props = defineProps<{
  connection: GatewayConnectionSettings;
  pluginPage: ActiveRemotePluginPage;
}>();

const loading = ref(false);
const error = ref("");
const document = ref<PluginPageDocument | null>(null);
const frameTitle = computed(
  () =>
    props.pluginPage.page.title ||
    `${props.pluginPage.pluginName} · ${props.pluginPage.page.id}`,
);
const srcdoc = computed(() =>
  document.value ? sandboxPluginPageDocument(document.value) : "",
);

watch(
  () => [
    props.pluginPage.pluginId,
    props.pluginPage.page.id,
    props.connection.gatewayWsUrl,
    props.connection.adminBearerToken,
  ] as const,
  async (_value, _previous, onCleanup) => {
    const controller = new AbortController();
    onCleanup(() => controller.abort());
    loading.value = true;
    error.value = "";
    document.value = null;
    try {
      document.value = await fetchDesktopPluginPage(
        props.connection,
        props.pluginPage,
        controller.signal,
      );
    } catch (reason) {
      if (!controller.signal.aborted) {
        error.value = reason instanceof Error ? reason.message : String(reason);
      }
    } finally {
      if (!controller.signal.aborted) loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <section class="panel plugin-page-panel">
    <header class="panel__header">
      <h2>{{ frameTitle }}</h2>
      <span>隔离插件页面</span>
    </header>
    <div class="plugin-page-panel__body">
      <p v-if="loading" class="plugin-page-panel__status">正在加载插件页面…</p>
      <p v-else-if="error" class="plugin-page-panel__error" role="alert">
        {{ error }}
      </p>
      <iframe
        v-else-if="srcdoc"
        class="plugin-page-panel__frame"
        :title="frameTitle"
        :srcdoc="srcdoc"
        sandbox="allow-scripts"
        referrerpolicy="no-referrer"
      />
    </div>
  </section>
</template>

<style scoped>
.plugin-page-panel__body {
  min-height: 320px;
  padding: 14px;
}

.plugin-page-panel__frame {
  display: block;
  width: 100%;
  min-height: 440px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: white;
}

.plugin-page-panel__status,
.plugin-page-panel__error {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.plugin-page-panel__error {
  color: #6b2a32;
}
</style>
