<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import type { TtsSettings } from "@/domain/config";

const props = defineProps<{
  settings: TtsSettings;
}>();

const emit = defineEmits<{
  update: [settings: TtsSettings];
  preview: [text: string];
}>();

const voices = ref<SpeechSynthesisVoice[]>([]);
const previewText = ref("你好，我是纳西妲。很高兴见到你。");
let synthesis: SpeechSynthesis | null = null;

const languages = computed(() => {
  const available = new Set(voices.value.map((voice) => voice.lang));
  available.add(props.settings.language);
  return [...available].filter(Boolean).sort((left, right) =>
    left.localeCompare(right),
  );
});

const matchingVoices = computed(() => {
  const requested = props.settings.language.toLowerCase().split("-")[0];
  return voices.value.filter(
    (voice) => voice.lang.toLowerCase().split("-")[0] === requested,
  );
});

const status = computed(() => {
  if (!synthesis) return "Web Speech unavailable";
  if (!voices.value.length) return "waiting for system voices";
  return `${voices.value.length} system voices`;
});

function refreshVoices() {
  voices.value = synthesis?.getVoices() ?? [];
}

function update(patch: Partial<TtsSettings>) {
  emit("update", {
    ...props.settings,
    ...patch,
  });
}

function changeLanguage(event: Event) {
  const language = (event.target as HTMLSelectElement).value;
  update({ language, voiceUri: "" });
}

function changeVoice(event: Event) {
  update({ voiceUri: (event.target as HTMLSelectElement).value });
}

function changeNumber(
  key: "rate" | "pitch" | "volume",
  event: Event,
) {
  update({ [key]: Number((event.target as HTMLInputElement).value) });
}

onMounted(() => {
  synthesis =
    typeof globalThis.speechSynthesis === "undefined"
      ? null
      : globalThis.speechSynthesis;
  refreshVoices();
  synthesis?.addEventListener("voiceschanged", refreshVoices);
});

onBeforeUnmount(() => {
  synthesis?.removeEventListener("voiceschanged", refreshVoices);
});
</script>

<template>
  <section class="panel tts-settings" aria-label="TTS settings">
    <header class="panel__header">
      <h2>System TTS</h2>
      <span>{{ status }}</span>
    </header>

    <div class="tts-settings__body">
      <label>
        <span>Language</span>
        <select :value="settings.language" @change="changeLanguage">
          <option v-for="language in languages" :key="language" :value="language">
            {{ language }}
          </option>
        </select>
      </label>

      <label>
        <span>Voice</span>
        <select :value="settings.voiceUri" @change="changeVoice">
          <option value="">
            Auto (prefer matching female voice)
          </option>
          <option
            v-for="voice in matchingVoices"
            :key="voice.voiceURI"
            :value="voice.voiceURI"
          >
            {{ voice.name }} · {{ voice.lang }}
            {{ voice.localService ? "· local" : "· online" }}
          </option>
        </select>
      </label>

      <label class="tts-settings__check">
        <input
          type="checkbox"
          :checked="settings.preferFemale"
          @change="update({
            preferFemale: ($event.target as HTMLInputElement).checked,
          })"
        />
        <span>Auto mode prefers common female voice names</span>
      </label>

      <label>
        <span>Rate: {{ settings.rate.toFixed(2) }}</span>
        <input
          type="range"
          min="0.5"
          max="1.5"
          step="0.05"
          :value="settings.rate"
          @input="changeNumber('rate', $event)"
        />
      </label>

      <label>
        <span>Pitch: {{ settings.pitch.toFixed(1) }} semitones</span>
        <input
          type="range"
          min="-6"
          max="6"
          step="0.5"
          :value="settings.pitch"
          @input="changeNumber('pitch', $event)"
        />
      </label>

      <label>
        <span>Volume: {{ Math.round(settings.volume * 100) }}%</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          :value="settings.volume"
          @input="changeNumber('volume', $event)"
        />
      </label>

      <p v-if="voices.length && !matchingVoices.length" class="tts-settings__warning">
        No {{ settings.language }} system voice was found. Install a Chinese
        speech voice in Windows language settings, then reopen the app.
      </p>

      <form class="tts-settings__preview" @submit.prevent="emit('preview', previewText)">
        <label for="tts-preview-text">Preview text</label>
        <textarea
          id="tts-preview-text"
          v-model="previewText"
          rows="3"
          lang="zh-CN"
        ></textarea>
        <button type="submit" :disabled="!previewText.trim() || !voices.length">
          Preview Voice
        </button>
      </form>

      <p class="tts-settings__note">
        Web Speech does not expose reliable gender metadata. Explicit voice
        selection is authoritative; female preference is only a name heuristic.
      </p>
    </div>
  </section>
</template>
