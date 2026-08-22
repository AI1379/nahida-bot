<script setup lang="ts">
import { computed } from "vue";

import type { TurnRecord, TurnStatus } from "@/stores/desktop";

const props = defineProps<{
  sessionId: string;
  turns: TurnRecord[];
}>();

const chronologicalTurns = computed(() => [...props.turns].reverse());

const statusLabels: Record<TurnStatus, string> = {
  submitting: "Sending",
  accepted: "Queued",
  generating: "Generating reply",
  synthesizing: "Preparing voice",
  playing: "Playing",
  completed: "Complete",
  failed: "Failed",
};
</script>

<template>
  <section class="runtime-conversation" aria-label="Current conversation">
    <header class="runtime-conversation__header">
      <div>
        <strong>Current conversation</strong>
        <span>{{ props.sessionId || "No session" }}</span>
      </div>
      <span>{{ props.turns.length }} turns</span>
    </header>

    <ol
      v-if="chronologicalTurns.length"
      class="runtime-conversation__list"
      aria-live="polite"
    >
      <li
        v-for="turn in chronologicalTurns"
        :key="turn.id"
        class="runtime-conversation__turn"
        :data-status="turn.status"
      >
        <div v-if="turn.userText" class="runtime-conversation__message is-user">
          <span>You</span>
          <p>{{ turn.userText }}</p>
        </div>
        <div
          v-if="turn.assistantText"
          class="runtime-conversation__message is-assistant"
        >
          <span>Nahida</span>
          <p>{{ turn.assistantText }}</p>
        </div>
        <div class="runtime-conversation__progress">
          <span class="runtime-conversation__status-dot" aria-hidden="true"></span>
          <span>{{ statusLabels[turn.status] }}</span>
          <time>{{ new Date(turn.updatedAt).toLocaleTimeString() }}</time>
        </div>
        <p v-if="turn.error" class="runtime-conversation__error" role="alert">
          {{ turn.error }}
        </p>
      </li>
    </ol>
    <p v-else class="runtime-conversation__empty">
      Send a message to start this conversation.
    </p>
  </section>
</template>
