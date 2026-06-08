<script setup lang="ts">
import { ref } from "vue";

const emit = defineEmits<{
  submit: [text: string];
  connect: [];
  disconnect: [];
}>();

defineProps<{
  connected: boolean;
  gatewayUrl: string;
}>();

const text = ref("演示一条带 TTS 和 Live2D 表现计划的回复。");

function submit() {
  emit("submit", text.value);
  text.value = "";
}
</script>

<template>
  <section class="panel controls" aria-label="Controls">
    <header class="panel__header">
      <h2>Mock Backend</h2>
      <span>{{ connected ? "connected" : "offline" }}</span>
    </header>

    <div class="controls__target">
      <label>Gateway</label>
      <code>{{ gatewayUrl }}</code>
    </div>

    <div class="controls__buttons">
      <button type="button" :disabled="connected" @click="emit('connect')">
        Connect
      </button>
      <button type="button" :disabled="!connected" @click="emit('disconnect')">
        Disconnect
      </button>
    </div>

    <form class="controls__form" @submit.prevent="submit">
      <label for="desktop-message">Message</label>
      <textarea
        id="desktop-message"
        v-model="text"
        :disabled="!connected"
        rows="4"
      ></textarea>
      <button type="submit" :disabled="!connected || !text.trim()">
        Send Mock Message
      </button>
    </form>
  </section>
</template>
