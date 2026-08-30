<script setup lang="ts">
import { computed } from "vue";

import type { TurnRecord, TurnStatus } from "@/stores/desktop";

const props = defineProps<{
  sessionId: string;
  turns: TurnRecord[];
  closable?: boolean;
}>();

defineEmits<{
  close: [];
}>();

const chronologicalTurns = computed(() => [...props.turns].reverse());

const statusLabels: Record<TurnStatus, string> = {
  submitting: "正在发送",
  accepted: "已进入队列",
  generating: "正在生成回复",
  synthesizing: "正在准备语音",
  playing: "正在播放",
  completed: "已完成",
  failed: "失败",
};
</script>

<template>
  <section
    id="runtime-conversation"
    class="runtime-conversation"
    aria-label="当前对话"
  >
    <header class="runtime-conversation__header">
      <div>
        <strong>当前对话</strong>
        <span>{{ props.sessionId || "尚未创建会话" }}</span>
      </div>
      <div class="runtime-conversation__header-actions">
        <span>{{ props.turns.length }} 条</span>
        <button
          v-if="props.closable"
          type="button"
          aria-label="收起对话"
          @click="$emit('close')"
        >
          收起
        </button>
      </div>
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
          <span>你</span>
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
      发送一条消息，开始与纳西妲对话。
    </p>
  </section>
</template>
