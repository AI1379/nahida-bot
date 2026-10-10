<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useDesktopStore } from "@/stores/desktop";
import { initialVoiceLabStats, VoiceLabClient } from "@/services/voiceLabClient";
import { HttpWavAsrProvider } from "@/services/voiceAsr";

const store = useDesktopStore();
const stats = ref(initialVoiceLabStats());
const busy = ref(false);
const threshold = ref(0.035);
const delayMs = ref(600);
const injectLate = ref(false);
const mode = ref("mock");
const asrEndpoint = ref("http://127.0.0.1:8765/v1/asr/transcriptions");
const asrToken = ref("");
const language = ref("zh");
const voice = ref("");
let client: VoiceLabClient | null = null;

function start(): void {
  client?.close();
  busy.value = true;
  stats.value = initialVoiceLabStats();
  try {
    const asr = mode.value === "real" ? new HttpWavAsrProvider({ endpoint: asrEndpoint.value,
      token: asrToken.value, language: language.value }) : undefined;
    client = new VoiceLabClient({
    gatewayUrl: store.gatewayConnection.gatewayWsUrl,
    token: store.gatewayConnection.adminBearerToken,
    asr, voice: voice.value,
    settings: () => ({ threshold: threshold.value, delayMs: delayMs.value, injectLate: injectLate.value }),
    onStats: (value) => {
      stats.value = value;
      if (value.error || value.status === "已结束") busy.value = false;
    },
    });
    void client.start();
  } catch (error) {
    busy.value = false;
    stats.value.error = error instanceof Error ? error.message : "无法启动语音实验";
  }
}

function stop(): void { client?.close(); busy.value = false; }
function simulate(): void { client?.simulateTurn(); }
function ms(value: number | null): string { return value === null ? "—" : `${value.toFixed(1)} ms`; }
onBeforeUnmount(stop);
</script>

<template>
  <section class="panel voice-lab" aria-label="Voice Lab">
    <h2>Voice Lab <small>实验模式</small></h2>
    <label>实验模式 <select v-model="mode" :disabled="busy">
      <option value="mock">模拟识别 / 测试音</option>
      <option value="real">真实 ASR / Gateway 语音回复</option>
    </select></label>
    <template v-if="mode === 'real'">
      <p>音频发送到所填 ASR 服务，转写文字发送到 Gateway。回复使用 Gateway 的模型、人设与 TTS 配置。</p>
      <p>当前说完后识别、完整合成后播放。保留本次通话上下文，不调用工具或写入持久记忆。</p>
      <label>ASR 地址 <input v-model="asrEndpoint" :disabled="busy" type="url" /></label>
      <label>ASR Token <input v-model="asrToken" :disabled="busy" type="password" autocomplete="off" /></label>
      <label>识别语言 <select v-model="language" :disabled="busy"><option value="zh">中文</option><option value="en">英语</option><option value="ja">日语</option><option value="auto">自动</option></select></label>
      <label>Gateway 音色名 <input v-model="voice" :disabled="busy" placeholder="留空使用默认音色" /></label>
    </template>
    <p v-else>模拟识别 / 测试音模式将 PCM 上传到 Gateway，不调用模型或写入对话记忆。</p>
    <p>结束或离开工作台会释放麦克风。建议先戴耳机测试。</p>
    <div class="actions">
      <button :disabled="busy" @click="start">开始实验</button>
      <button :disabled="!busy" @click="stop">结束</button>
      <button v-if="mode === 'mock'" :disabled="!stats.active" @click="simulate">模拟发言 / 插话</button>
    </div>
    <p role="status">{{ stats.status }}</p>
    <p v-if="stats.error" role="alert" class="error">{{ stats.error }}</p>
    <label>麦克风音量 <meter :value="stats.rms" :max="0.25" /> {{ stats.rms.toFixed(3) }}</label>
    <label>开口阈值 {{ threshold.toFixed(3) }}
      <input v-model.number="threshold" type="range" min="0.005" max="0.15" step="0.005" />
    </label>
    <label v-if="mode === 'mock'">模拟响应等待 {{ delayMs }} ms
      <input v-model.number="delayMs" type="range" min="0" max="3000" step="100" />
    </label>
    <label v-if="mode === 'mock'"><input v-model="injectLate" type="checkbox" /> 插话时注入上一轮迟到音频</label>
    <dl>
      <dt>本地清队列（最近 / p95）</dt><dd>{{ ms(stats.stopMs) }} / {{ ms(stats.stopP95Ms) }}</dd>
      <dt>发言结束 → 首次渲染</dt><dd>{{ ms(stats.firstPlaybackMs) }}</dd>
      <template v-if="mode === 'real'"><dt>发言结束 → ASR 完成</dt><dd>{{ ms(stats.asrMs) }}</dd></template>
      <dt>播放队列</dt><dd>{{ stats.queueMs.toFixed(0) }} ms / 500 ms</dd>
      <dt>上传 Gateway PCM 帧 / 丢弃旧音频</dt><dd>{{ stats.sentFrames }} / {{ stats.dropped }}</dd>
      <dt>播放欠载次数</dt><dd>{{ stats.underruns }}</dd>
    </dl>
    <p class="note">延迟来自软件事件，尚未包含设备输出延迟；清队列计时从检测到开口开始。简单能量检测可能受外放回声影响。</p>
    <p v-if="stats.transcript">{{ stats.transcript }}</p>
    <p v-if="stats.output">{{ stats.output }}</p>
  </section>
</template>

<style scoped>
.voice-lab { display: grid; gap: 0.7rem; padding: 1rem; }
.voice-lab h2, .voice-lab p { margin: 0; }
.voice-lab small, .note { opacity: 0.7; font-size: 0.8rem; }
.voice-lab label { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.voice-lab dl { display: grid; grid-template-columns: 1fr auto; gap: 0.4rem; margin: 0; }
.voice-lab dd { margin: 0; font-variant-numeric: tabular-nums; }
.error { color: #dc5364; }
</style>
