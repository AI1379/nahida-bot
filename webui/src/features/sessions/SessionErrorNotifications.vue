<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useSessionErrorNotifications, useSetSessionErrorNotifications } from "@/api/queries";
import type { ErrorNotificationMode } from "@/api/schemas";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";

const props = defineProps<{ sessionId: string }>();
const query = useSessionErrorNotifications(computed(() => props.sessionId));
const save = useSetSessionErrorNotifications();
const mode = ref<ErrorNotificationMode>("interactive");
watch(() => query.data.value, (value) => { mode.value = value?.mode ?? "interactive"; }, { immediate: true });
const disabled = computed(() => !query.data.value || save.isPending.value || mode.value === query.data.value.mode);
</script>

<template>
  <section class="error-settings">
    <div>
      <strong>Runtime error notifications</strong>
      <p>Controls failure and interruption notices for this session. Normal replies are unchanged; failures remain in logs.</p>
    </div>
    <Alert v-if="query.error.value" variant="destructive">Failed to load settings: {{ query.error.value.message }}</Alert>
    <div class="controls">
      <select v-model="mode" aria-label="Runtime error notifications" :disabled="!query.data.value || save.isPending.value">
        <option value="interactive">Direct interactions only (default)</option>
        <option value="none">Silent for all triggers</option>
        <option value="all">Show for all triggers</option>
      </select>
      <Button size="sm" variant="outline" :disabled="disabled" @click="save.mutate({ sessionId, mode })">
        {{ save.isPending.value ? "Saving..." : "Save" }}
      </Button>
    </div>
    <p class="hint">By default, private messages, @ mentions and commands show notices; Conversation Joiner, other unaddressed group triggers and scheduled runs stay quiet. A new session starts with the default.</p>
  </section>
</template>

<style scoped>
.error-settings { padding: 0.9rem 1rem; border-bottom: 1px solid var(--color-border); background: var(--color-card); display: grid; gap: 0.6rem; }
strong { font-size: 0.8125rem; }
p { margin: 0.25rem 0 0; color: var(--color-muted-foreground); font-size: 0.75rem; line-height: 1.5; }
.controls { display: flex; gap: 0.5rem; flex-wrap: wrap; }
select { min-width: 0; max-width: 100%; padding: 0.4rem 0.6rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-background); color: var(--color-foreground); font-size: 0.8125rem; }
</style>
