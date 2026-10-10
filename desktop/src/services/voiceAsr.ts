/** Recognition capability is independent of deployment location and transport. */
export interface VoiceAsrResult { text: string; language?: string }
export interface VoiceAsrSession {
  pushAudio(pcm: ArrayBuffer): void;
  finish(): Promise<VoiceAsrResult>;
  cancel(): void;
}
export interface VoiceAsrProvider {
  readonly streaming: boolean;
  openTurn(onPartial?: (text: string) => void): VoiceAsrSession;
}

export interface HttpAsrSettings { endpoint: string; token: string; language: string }

export function pcmWav(frames: ArrayBuffer[]): ArrayBuffer {
  const size = frames.reduce((sum, frame) => sum + frame.byteLength, 0);
  const data = new ArrayBuffer(44 + size);
  const view = new DataView(data);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF"); view.setUint32(4, size + 36, true); text(8, "WAVEfmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, size, true);
  let offset = 44;
  for (const frame of frames) { new Uint8Array(data, offset, frame.byteLength).set(new Uint8Array(frame)); offset += frame.byteLength; }
  return data;
}

/** First adapter: bounded utterances over HTTP, usable at localhost or remotely. */
export class HttpWavAsrProvider implements VoiceAsrProvider {
  readonly streaming = false;
  private readonly settings: HttpAsrSettings;
  constructor(settings: HttpAsrSettings) {
    const url = new URL(settings.endpoint);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("ASR 地址必须为 HTTP(S)");
    if (!settings.token.trim()) throw new Error("请填写独立的 ASR Token");
    this.settings = { ...settings };
  }
  openTurn(): VoiceAsrSession { return new HttpWavAsrSession(this.settings); }
}

class HttpWavAsrSession implements VoiceAsrSession {
  private frames: ArrayBuffer[] = [];
  private bytes = 0;
  private ended = false;
  private controller = new AbortController();
  private readonly settings: HttpAsrSettings;
  constructor(settings: HttpAsrSettings) { this.settings = settings; }
  pushAudio(pcm: ArrayBuffer): void {
    if (this.ended) return;
    if (pcm.byteLength !== 640) throw new Error("ASR 输入必须为 20ms PCM16");
    if (this.bytes + pcm.byteLength > 960000) throw new Error("单次发言超过 30 秒，请分段讲话");
    this.frames.push(pcm.slice(0)); this.bytes += pcm.byteLength;
  }
  async finish(): Promise<VoiceAsrResult> {
    if (this.ended) throw new Error("ASR turn already ended");
    this.ended = true;
    if (!this.bytes) return { text: "" };
    const wav = pcmWav(this.frames);
    this.frames = [];
    const url = new URL(this.settings.endpoint);
    url.searchParams.set("language", this.settings.language);
    const timer = setTimeout(() => this.controller.abort("ASR timeout"), 60000);
    try {
      const response = await this.request(url, wav);
      if (!response.ok) throw new Error(`ASR 请求失败（${response.status}）`);
      const result = await response.json();
      if (typeof result.text !== "string" || result.text.length > 2000) throw new Error("无效 ASR 转写响应");
      return { text: result.text.trim(), language: result.language };
    } finally { clearTimeout(timer); }
  }

  private async request(url: URL, wav: ArrayBuffer): Promise<Response> {
    // A cancelled native inference may still hold the server's single worker.
    // Retry only explicit busy responses, under the same timeout/abort signal.
    while (!this.controller.signal.aborted) {
      const response = await fetch(url, {
        method: "POST", headers: { "Content-Type": "audio/wav", Authorization: `Bearer ${this.settings.token}` },
        body: wav, signal: this.controller.signal, redirect: "error", credentials: "omit",
      });
      if (response.status !== 429) return response;
      await response.body?.cancel();
      await new Promise<void>((resolve, reject) => {
        const abort = () => { clearTimeout(timer); reject(new Error("ASR cancelled")); };
        const timer = setTimeout(() => {
          this.controller.signal.removeEventListener("abort", abort); resolve();
        }, 250);
        this.controller.signal.addEventListener("abort", abort, { once: true });
        if (this.controller.signal.aborted) abort();
      });
    }
    throw new Error("ASR cancelled or timed out");
  }
  cancel(): void { this.ended = true; this.frames = []; this.controller.abort(); }
}
