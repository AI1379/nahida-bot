<script setup lang="ts">
import { ref } from "vue";

import { mockControlDefaults } from "@/config/mockDefaults";

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

const text = ref(mockControlDefaults.message);
const mockLlmResult = ref(mockControlDefaults.llmResult);

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
      <h2>Gateway Node</h2>
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
        Send Message
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
