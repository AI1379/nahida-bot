<script setup lang="ts">
import { computed } from "vue";
import { useMemoryDetail } from "@/api/queries";
import Alert from "@/components/ui/Alert.vue";
import Badge from "@/components/ui/Badge.vue";
import { formatDateTime } from "@/lib/utils";

const props = defineProps<{ itemId: string }>();
const query = useMemoryDetail(computed(() => props.itemId));
const detail = computed(() => query.data.value);
</script>

<template>
  <section class="evidence-panel" aria-label="Memory attribution and evidence">
    <h3>Subject &amp; evidence</h3>
    <Alert v-if="query.error.value" variant="destructive">Failed to load evidence: {{ query.error.value.message }}</Alert>
    <p v-else-if="query.isLoading.value">Loading original evidence...</p>
    <template v-else-if="detail">
      <div class="attribution"><span>About</span><code>{{ detail.item.subject_label }}</code><span>Portable</span><b>{{ detail.portable ? 'Yes' : 'No' }}</b><span>Status</span><b>{{ detail.item.status }}</b></div>
      <p v-if="detail.subject.kind === 'unknown'" class="notice">The subject is unknown. The current reader or storage scope does not establish whose experience this is.</p>
      <p v-if="detail.replaces_item_id">Replaces <code>{{ detail.replaces_item_id }}</code></p>
      <p v-if="!detail.evidence.length" class="notice">No structured evidence references are recorded. Legacy metadata remains available below; this does not prove the memory is false.</p>
      <article v-for="(evidence, index) in detail.evidence" :key="index" class="evidence-entry">
        <div class="evidence-heading"><Badge :variant="evidence.status === 'available' ? 'outline' : 'warning'">{{ evidence.status }}</Badge><span>{{ evidence.reference.role }} · {{ evidence.reference.display_name || evidence.reference.account_key || 'unknown speaker' }}</span></div>
        <div class="provenance"><code>{{ evidence.reference.account_key || 'No account recorded' }}</code><code>{{ evidence.reference.chat_id || evidence.reference.session_id }}</code><span>{{ evidence.reference.timestamp ? formatDateTime(evidence.reference.timestamp) : 'No time recorded' }}</span><span>Turn {{ evidence.turn?.turn_id || evidence.reference.turn_id || 'unlinked' }} · Message {{ evidence.reference.message_id || 'unlinked' }}</span></div>
        <pre v-if="evidence.turn">{{ evidence.turn.content }}</pre>
        <p v-else-if="evidence.status === 'mismatch'" class="notice">The stored reference conflicts with the original message. Its text is not shown as evidence.</p>
        <p v-else>Original text is unavailable or has not been linked. The reference alone is not a verified transcript.</p>
        <p v-if="evidence.excerpt_truncated">Showing the first 12,000 characters of this message.</p>
      </article>
      <p v-if="detail.evidence_truncated">Showing the first 50 evidence references.</p>
    </template>
  </section>
</template>

<style scoped>
.evidence-panel { display: grid; gap: 0.8rem; }
h3 { margin: 0; font-size: 0.875rem; font-weight: 600; }
p { margin: 0; font-size: 0.75rem; line-height: 1.6; color: var(--color-muted-foreground); }
.attribution { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 0.65rem; font-size: 0.75rem; }
.attribution span { color: var(--color-muted-foreground); }
code { overflow-wrap: anywhere; }
.evidence-entry { display: grid; gap: 0.6rem; padding: 0.85rem; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-background); }
.evidence-heading { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; font-size: 0.75rem; }
.provenance { display: flex; flex-wrap: wrap; gap: 0.35rem 1rem; font-size: 0.7rem; color: var(--color-muted-foreground); }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; font-size: 0.8125rem; line-height: 1.65; max-height: 22rem; overflow-y: auto; }
.notice { padding: 0.65rem; border-left: 2px solid var(--color-border); background: var(--color-muted); border-radius: var(--radius-sm); }
</style>
