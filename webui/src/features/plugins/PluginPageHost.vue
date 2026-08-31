<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { api, toApiError } from "@/api/client";
import type {
  PluginPageContribution,
  PluginPageDocument,
} from "@/api/schemas";

const props = defineProps<{
  pluginId: string;
  pluginName: string;
  page: PluginPageContribution;
}>();

const loading = ref(false);
const error = ref("");
const document = ref<PluginPageDocument | null>(null);
const frameTitle = computed(
  () => props.page.title || `${props.pluginName} · ${props.page.id}`,
);
const srcdoc = computed(() =>
  document.value ? sandboxDocument(document.value) : "",
);

watch(
  () => [props.pluginId, props.page.id] as const,
  async (_value, _previous, onCleanup) => {
    let active = true;
    onCleanup(() => {
      active = false;
    });
    loading.value = true;
    error.value = "";
    document.value = null;
    try {
      const response = await api.get<PluginPageDocument>(
        `/plugins/${encodeURIComponent(props.pluginId)}/pages/${encodeURIComponent(props.page.id)}`,
      );
      if (!active) return;
      if (response.target !== "webui.admin") {
        throw new Error(`Page target ${response.target} cannot run in WebUI`);
      }
      document.value = response;
    } catch (reason) {
      if (active) error.value = toApiError(reason).detail;
    } finally {
      if (active) loading.value = false;
    }
  },
  { immediate: true },
);

function sandboxDocument(page: PluginPageDocument): string {
  const context = JSON.stringify({
    version: 1,
    plugin: { id: page.plugin_id, name: page.plugin_name },
    page: { id: page.page_id, target: page.target, title: page.title },
  }).replaceAll("<", "\\u003c");
  const head = `
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; font-src data:; media-src data:; object-src 'none'; base-uri 'none'; form-action 'none'">
    <meta name="referrer" content="no-referrer">
    <script>(()=>{const value=${context};Object.freeze(value.plugin);Object.freeze(value.page);window.__NAHIDA_PLUGIN_CONTEXT__=Object.freeze(value)})()<\/script>
  `;
  if (/<head(?:\s[^>]*)?>/i.test(page.html)) {
    return page.html.replace(/<head(?:\s[^>]*)?>/i, (match) => `${match}${head}`);
  }
  return `<!doctype html><html><head>${head}</head><body>${page.html}</body></html>`;
}
</script>

<template>
  <div class="plugin-page-host">
    <p v-if="loading" class="plugin-page-status">Loading plugin page…</p>
    <p v-else-if="error" class="plugin-page-error" role="alert">{{ error }}</p>
    <iframe
      v-else-if="srcdoc"
      class="plugin-page-frame"
      :title="frameTitle"
      :srcdoc="srcdoc"
      sandbox="allow-scripts"
      referrerpolicy="no-referrer"
    />
  </div>
</template>

<style scoped>
.plugin-page-host {
  min-height: 280px;
}

.plugin-page-frame {
  display: block;
  width: 100%;
  min-height: 420px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: white;
}

.plugin-page-status,
.plugin-page-error {
  color: var(--color-muted-foreground);
}

.plugin-page-error {
  color: var(--color-destructive);
}
</style>
