<script setup lang="ts">
import { ref } from "vue";

const emit = defineEmits<{
  submit: [text: string];
  submitMockLlmResult: [rawOutput: string];
  connect: [];
  disconnect: [];
}>();

defineProps<{
  connected: boolean;
  gatewayUrl: string;
}>();

const text = ref("演示一条带 TTS 和 Live2D 表现计划的回复。");
const mockLlmResult = ref(`{
  "text": "今天的计划已经整理好了。先处理配置问题，然后再看桌宠协议。",
  "segments": [
    {
      "text": "今天的计划已经整理好了。",
      "emotion": "happy",
      "motion": "nod",
      "pause_after_ms": 250
    },
    {
      "text": "先处理配置问题，然后再看桌宠协议。",
      "emotion": "thinking",
      "expression": "star",
      "motion": "point"
    }
  ]
}`);

function submit() {
  emit("submit", text.value);
  text.value = "";
}

function submitMockLlmResult() {
  emit("submitMockLlmResult", mockLlmResult.value);
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

    <form class="controls__form" @submit.prevent="submitMockLlmResult">
      <label for="mock-llm-result">Mock LLM Result</label>
      <textarea
        id="mock-llm-result"
        v-model="mockLlmResult"
        :disabled="!connected"
        rows="9"
        spellcheck="false"
      ></textarea>
      <button type="submit" :disabled="!connected || !mockLlmResult.trim()">
        Apply Mock LLM Result
      </button>
    </form>
  </section>
</template>
