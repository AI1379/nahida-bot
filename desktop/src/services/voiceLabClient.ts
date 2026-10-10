/** Desktop lab runtime. Each instance is single-use and owns its audio/socket. */
import { gatewayWsUrlToHttpBase } from "@/domain/gatewayConnection";
import { VoiceLabAudio, type LabAudioEvent } from "./voiceLabAudio";
import { VoiceLabPacketGate, VoiceLabVad } from "./voiceLabProtocol";
import { VoiceReflexExecutor, type ReflexCommand } from "./voiceReflexExecutor";
import type { VoiceAsrProvider, VoiceAsrSession } from "./voiceAsr";

type LabServerEvent = { epoch: number } & (
  | { type: "ready"; session_id: string; mode?: string; tts_mode?: string }
  | { type: "listening"; turn_id: string }
  | { type: "transcript" | "output"; text: string }
  | { type: "output_done" | "idle" }
  | { type: "reflex"; command: ReflexCommand }
  | { type: "turn_error"; message: string }
  | { type: "pong" }
);

export interface VoiceLabStats {
  status: string;
  active: boolean;
  rms: number;
  queueMs: number;
  sentFrames: number;
  dropped: number;
  underruns: number;
  stopMs: number | null;
  stopP95Ms: number | null;
  firstPlaybackMs: number | null;
  transcript: string;
  output: string;
  error: string;
  asrMs: number | null;
}

export const initialVoiceLabStats = (): VoiceLabStats => ({
  status: "未连接", active: false, rms: 0, queueMs: 0, sentFrames: 0, dropped: 0, underruns: 0,
  stopMs: null, stopP95Ms: null, firstPlaybackMs: null, transcript: "", output: "", error: "",
  asrMs: null,
});

interface LabOptions {
  gatewayUrl: string;
  token: string;
  settings: () => { threshold: number; delayMs: number; injectLate: boolean };
  onStats: (stats: VoiceLabStats) => void;
  asr?: VoiceAsrProvider;
  voice?: string;
}

export class VoiceLabClient {
  private audio = new VoiceLabAudio();
  private gate = new VoiceLabPacketGate();
  private vad = new VoiceLabVad();
  private socket: WebSocket | null = null;
  private reflex: VoiceReflexExecutor | null = null;
  private disposed = false;
  private ready = false;
  private stats = initialVoiceLabStats();
  private stopAt = 0;
  private endpointAt = 0;
  private playbackActive = false;
  private stopSamples: number[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private asrTurn: VoiceAsrSession | null = null;
  private preRoll: ArrayBuffer[] = [];

  private readonly options: LabOptions;

  constructor(options: LabOptions) { this.options = options; }

  async start(): Promise<void> {
    this.stats.status = "等待麦克风权限";
    this.publish();
    try {
      const base = gatewayWsUrlToHttpBase(this.options.gatewayUrl);
      if (!base || !this.options.token) throw new Error("请先在连接设置中填写 Gateway 地址和管理员 Token");
      await this.audio.open((event) => this.onAudio(event));
      if (this.disposed) return;
      const url = new URL(`${base}/api/voice/lab`);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      this.stats.status = "连接语音服务";
      this.publish();
      this.timer = setTimeout(() => this.fail("语音服务连接超时"), 10000);
      socket.onopen = () => socket.send(JSON.stringify({ token: this.options.token,
        mode: this.options.asr ? "external_asr" : "mock", voice: this.options.voice ?? "" }));
      socket.onmessage = ({ data }) => {
        try { this.onMessage(data); }
        catch (error) { this.fail(error instanceof Error ? error.message : "无效语音响应"); }
      };
      socket.onerror = () => this.fail("语音 WebSocket 连接失败");
      socket.onclose = ({ code, reason }) => {
        if (!this.disposed) this.fail(`语音连接关闭（${code}）${reason ? `：${reason}` : ""}`);
      };
    } catch (error) {
      if (!this.disposed) this.fail(error instanceof Error ? error.message : "无法开启麦克风");
    }
  }

  /** Immediate local invalidation, independent of server response latency. */
  private speechStarted(): void {
    this.reflex?.interruptLocally(this.gate.turnId);
    const epoch = this.gate.advance();
    this.stopAt = this.playbackActive ? performance.now() : 0;
    this.playbackActive = false;
    this.endpointAt = 0;
    this.audio.reset(epoch);
    this.send("speech_started");
    this.asrTurn?.cancel();
    this.asrTurn = this.options.asr?.openTurn((text) => {
      if (this.disposed || epoch !== this.gate.epoch) return;
      this.stats.transcript = text;
      this.send("transcript_partial", { text });
      this.publish();
    }) ?? null;
    for (const frame of this.preRoll) this.asrTurn?.pushAudio(frame);
    this.preRoll = [];
    this.stats.status = "正在听";
    this.publish();
  }

  private speechStopped(): void {
    this.endpointAt = performance.now();
    const settings = this.options.settings();
    this.send("speech_stopped", { delay_ms: settings.delayMs, inject_late: settings.injectLate });
    if (this.options.asr && this.asrTurn) void this.finishAsr(this.asrTurn, this.gate.epoch);
    this.stats.status = this.options.asr ? "ASR 正在识别" : "模拟识别 / 等待输出";
    this.publish();
  }

  simulateTurn(): void {
    if (!this.ready || this.options.asr) return;
    this.speechStarted();
    this.speechStopped();
  }

  private async finishAsr(turn: VoiceAsrSession, epoch: number): Promise<void> {
    const started = performance.now();
    try {
      const result = await turn.finish();
      if (this.disposed || epoch !== this.gate.epoch) return;
      this.stats.asrMs = performance.now() - started;
      this.stats.transcript = result.text;
      this.send("transcript_final", { text: result.text });
      this.stats.status = result.text ? "Gateway 正在生成回复 / 合成完整音频" : "未识别到文字，继续收音";
      this.publish();
    } catch (error) {
      if (!this.disposed && epoch === this.gate.epoch) this.fail(error instanceof Error ? error.message : "ASR 失败");
    }
  }

  private onAudio(event: LabAudioEvent): void {
    if (this.disposed) return;
    if (event.type === "device_closed") {
      this.fail("麦克风已断开或权限被撤销");
      return;
    }
    if (!this.ready) return;
    if (event.type === "capture") {
      this.capture(event);
      return;
    }
    if (event.type === "overflow") {
      this.fail("音频队列超过 500ms 或音频处理器异常，会话已停止");
      return;
    }
    if (event.epoch !== this.gate.epoch) return;
    if (event.type === "stopped" && this.stopAt) {
      this.stats.stopMs = performance.now() - this.stopAt;
      this.stopAt = 0;
      this.stopSamples.push(this.stats.stopMs);
      if (this.stopSamples.length > 100) this.stopSamples.shift();
      const ordered = [...this.stopSamples].sort((a, b) => a - b);
      this.stats.stopP95Ms = ordered[Math.ceil(ordered.length * 0.95) - 1] ?? null;
    } else if (event.type === "started") {
      this.playbackActive = true;
      this.stats.firstPlaybackMs = this.endpointAt ? performance.now() - this.endpointAt : null;
      this.stats.status = this.options.asr ? "播放语音（可直接插话）" : "播放测试音（可直接插话）";
      this.send("playback_started");
    } else if (event.type === "done") {
      this.playbackActive = false;
      this.send("playback_done");
    } else if (event.type === "underrun") {
      this.stats.underruns++;
    }
    this.publish();
  }

  private capture(event: LabAudioEvent): void {
    this.stats.rms = event.rms ?? 0;
    this.stats.queueMs = event.queued_ms ?? 0;
    const edge = this.vad.update(this.stats.rms, this.options.settings().threshold);
    try {
      if (edge === "start") this.speechStarted();
      if (this.options.asr && event.pcm) {
        if (this.vad.speaking) this.asrTurn?.pushAudio(event.pcm);
        else { this.preRoll.push(event.pcm); if (this.preRoll.length > 10) this.preRoll.shift(); }
      }
      if (edge === "stop") this.speechStopped();
    } catch (error) { this.fail(error instanceof Error ? error.message : "ASR 音频输入失败"); return; }
    if (!this.options.asr && this.socket?.readyState === WebSocket.OPEN && event.pcm) {
      if (this.socket.bufferedAmount > 32000) {
        this.fail("上传积压超过 1 秒，会话已停止");
        return;
      }
      this.socket.send(event.pcm);
      this.stats.sentFrames++;
    }
    this.publish();
  }

  private onMessage(data: string | ArrayBuffer): void {
    if (this.disposed) return;
    if (data instanceof ArrayBuffer) {
      const packet = this.gate.decode(data);
      this.stats.dropped = this.gate.dropped;
      if (packet) {
        this.reflex?.interruptLocally(this.gate.turnId, "formal_audio");
        if (packet.kind === "pcm") this.audio.push(this.gate.epoch, packet.pcm);
        else {
          const epoch = this.gate.epoch;
          void this.audio.playArtifact(epoch, packet.data).catch(() => {
            if (!this.disposed && epoch === this.gate.epoch) this.fail("TTS 音频解码失败");
          });
        }
      }
      this.publish();
      return;
    }
    const event = JSON.parse(data) as LabServerEvent;
    if (event.type === "ready") {
      if (this.ready || typeof event.session_id !== "string") throw new Error("Invalid session handshake");
      if (this.options.asr && (event.mode !== "external_asr" || event.tts_mode !== "artifact")) {
        throw new Error("Gateway 尚未支持真实 ASR/TTS 模式，请更新服务端");
      }
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.ready = true;
      this.heartbeat = setInterval(() => this.send("ping"), 15000);
      const finished = (command: { command_id: string }) => this.send("reflex_finished", { command_id: command.command_id });
      this.reflex = new VoiceReflexExecutor(event.session_id, this.audio.reflexPlayer(), {
        onCueComplete: finished, onCueCancelled: finished, onCommandDropped: finished,
      });
      this.stats.active = true;
      this.stats.status = "已连接，持续收音";
    } else if (event.epoch === this.gate.epoch) {
      this.handleControl(event);
    }
    this.publish();
  }

  private handleControl(event: LabServerEvent): void {
    switch (event.type) {
      case "listening": this.gate.turnId = event.turn_id; break;
      case "transcript": this.stats.transcript = event.text; break;
      case "output": this.stats.output = event.text; break;
      case "output_done": this.audio.finish(this.gate.epoch); break;
      case "idle": this.stats.status = "已连接，持续收音"; break;
      case "reflex": this.reflex?.handleCommand(event.command); break;
      case "turn_error": this.stats.status = event.message; break;
    }
  }

  private send(type: string, fields: Record<string, unknown> = {}): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type, epoch: this.gate.epoch, ...fields }));
    }
  }

  private publish(): void { this.options.onStats({ ...this.stats }); }

  private fail(message: string): void {
    this.stats.error = message;
    this.close();
  }

  close(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.ready = false;
    if (this.timer) clearTimeout(this.timer);
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.asrTurn?.cancel();
    this.preRoll = [];
    this.reflex?.dispose();
    this.audio.close();
    if (this.socket) {
      this.socket.onmessage = this.socket.onopen = this.socket.onerror = this.socket.onclose = null;
      this.socket.close();
    }
    this.socket = null;
    this.stats.active = false;
    this.stats.rms = this.stats.queueMs = 0;
    this.stats.status = this.stats.error ? "会话异常结束" : "已结束";
    this.publish();
  }
}
