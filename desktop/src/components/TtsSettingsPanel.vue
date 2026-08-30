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
  if (!synthesis) return "系统语音不可用";
  if (!voices.value.length) return "正在读取系统语音";
  return `${voices.value.length} 个系统语音`;
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
  <section class="panel tts-settings" aria-label="语音设置">
    <header class="panel__header">
      <h2>系统语音</h2>
      <span>{{ status }}</span>
    </header>

    <div class="tts-settings__body">
      <label>
        <span>语言</span>
        <select :value="settings.language" @change="changeLanguage">
          <option v-for="language in languages" :key="language" :value="language">
            {{ language }}
          </option>
        </select>
      </label>

      <label>
        <span>声音</span>
        <select :value="settings.voiceUri" @change="changeVoice">
          <option value="">
            自动（优先匹配女性声音）
          </option>
          <option
            v-for="voice in matchingVoices"
            :key="voice.voiceURI"
            :value="voice.voiceURI"
          >
            {{ voice.name }} · {{ voice.lang }}
            {{ voice.localService ? "· 本地" : "· 在线" }}
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
        <span>自动模式优先选择常见女性声音名称</span>
      </label>

      <label>
        <span>语速：{{ settings.rate.toFixed(2) }}</span>
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
        <span>音高：{{ settings.pitch.toFixed(1) }} 个半音</span>
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
        <span>音量：{{ Math.round(settings.volume * 100) }}%</span>
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
        未找到 {{ settings.language }} 系统语音。请在 Windows 语言设置中安装中文语音，
        然后重新打开应用。
      </p>

      <form class="tts-settings__preview" @submit.prevent="emit('preview', previewText)">
        <label for="tts-preview-text">试听文本</label>
        <textarea
          id="tts-preview-text"
          v-model="previewText"
          rows="3"
          lang="zh-CN"
        ></textarea>
        <button
          class="settings-button settings-button--primary"
          type="submit"
          :disabled="!previewText.trim() || !voices.length"
        >
          试听声音
        </button>
      </form>

      <p class="tts-settings__note">
        系统语音不会提供可靠的性别信息。手动选择的声音始终优先；女性声音偏好仅按名称推测。
      </p>
    </div>
  </section>
</template>
