<script setup lang="ts">
import type { ReadingNotification } from "@/services/readingNotifications";

defineProps<{ notification: ReadingNotification }>();
defineEmits<{ close: [] }>();
</script>

<template>
  <section class="reading-bubble" role="region" aria-label="桌宠阅读气泡" @dblclick.stop>
    <header>
      <strong>{{ notification.title }}</strong>
      <button type="button" aria-label="关闭阅读气泡" @click="$emit('close')">关闭</button>
    </header>
    <div class="reading-bubble__body" tabindex="0">{{ notification.body }}</div>
    <footer>静音阅读 · 关闭不会完成任务</footer>
  </section>
</template>

<style scoped>
.reading-bubble {
  position: absolute; top: 10px; left: 12px; right: 12px; z-index: 40;
  max-height: calc(100% - 100px); display: flex; flex-direction: column;
  color: #233e30; background: #f8fcf7f5; border: 1px solid #b7cbb5;
  border-radius: 18px; box-shadow: 0 8px 24px #163b2426; padding: 16px;
  pointer-events: auto;
}
header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
button { cursor: pointer; padding: 5px 10px; border-radius: 8px; border: 1px solid #b7cbb5; background: white; }
.reading-bubble__body { overflow-y: auto; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; margin: 12px 0; min-height: 0; }
footer { font-size: 11px; color: #617469; }
</style>
