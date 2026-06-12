<script setup lang="ts">
import type { TranscriptEntry } from "@/stores/desktop";

defineProps<{
  entries: TranscriptEntry[];
}>();
</script>

<template>
  <section class="panel transcript" aria-label="Transcript">
    <header class="panel__header">
      <h2>Transcript</h2>
      <span>{{ entries.length }} events</span>
    </header>

    <ol class="transcript__list">
      <li v-for="entry in entries" :key="entry.id" class="transcript__item">
        <div class="transcript__meta">
          <span>{{ entry.role }}</span>
          <time>{{ new Date(entry.at).toLocaleTimeString() }}</time>
        </div>
        <p>{{ entry.text }}</p>
      </li>
    </ol>
  </section>
</template>
