<script setup lang="ts">
import { onMounted, ref } from "vue";
import { invoke, isTauri } from "@tauri-apps/api/core";

const quiet = ref(false);
const error = ref("");
onMounted(async () => {
  if (!isTauri()) return;
  try {
    const state = await invoke<{ do_not_disturb: boolean }>("desktop_context_read");
    quiet.value = state.do_not_disturb;
  } catch (reason) { error.value = String(reason); }
});
async function toggle() {
  try {
    await invoke("desktop_context_set_quiet", { quiet: !quiet.value });
    quiet.value = !quiet.value;
    error.value = "";
  } catch (reason) { error.value = String(reason); }
}
</script>

<template>
  <div>
    <button type="button" :disabled="!isTauri()" :aria-pressed="quiet" @click="toggle">
      {{ quiet ? "关闭通知勿扰" : "开启通知勿扰" }}
    </button>
    <small v-if="error" role="alert">{{ error }}</small>
  </div>
</template>
