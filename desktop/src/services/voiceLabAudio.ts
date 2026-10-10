/** Session-owned audio resources; microphone tracks always stop on disposal. */
import type { LocalReflexClipPlayer } from "./voiceReflexExecutor";
import workletUrl from "./voiceLab.worklet.js?url&no-inline";
import { VoiceArtifactPlayback } from "./voiceArtifactPlayback";

export interface LabAudioEvent {
  type: "capture" | "stopped" | "started" | "done" | "overflow" | "underrun" | "device_closed";
  epoch: number;
  pcm?: ArrayBuffer;
  rms?: number;
  queued_ms?: number;
}

export class VoiceLabAudio {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private disposed = false;
  private cue: AudioBufferSourceNode | null = null;
  private cueDone: (() => void) | null = null;
  private cueBuffer: AudioBuffer | null = null;
  private artifacts = new VoiceArtifactPlayback({
    decode: (data) => {
      if (!this.context) return Promise.reject(new Error("Audio session is closed"));
      return this.context.decodeAudioData(data);
    },
    push: (epoch, pcm) => this.push(epoch, pcm),
    finish: (epoch) => this.finish(epoch),
  });

  async open(onEvent: (event: LabAudioEvent) => void): Promise<void> {
    try {
      const context = new AudioContext({ latencyHint: "interactive" });
      this.context = context;
      // Resume while still in the user gesture; permission may resolve much later.
      await context.resume();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      if (this.disposed) {
        stream.getTracks().forEach((track) => track.stop());
        throw new Error("Voice Lab 已结束");
      }
      this.stream = stream;
      stream.getTracks().forEach((track) => {
        track.onended = () => onEvent({ type: "device_closed", epoch: -1 });
      });
      await context.audioWorklet.addModule(workletUrl);
      if (this.disposed) throw new Error("Voice Lab 已结束");
      const node = new AudioWorkletNode(context, "nahida-voice-lab", {
        numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      });
      this.node = node;
      node.port.onmessage = ({ data }: MessageEvent<LabAudioEvent>) => onEvent(data);
      node.onprocessorerror = () => onEvent({ type: "overflow", epoch: -1 });
      this.source = context.createMediaStreamSource(stream);
      this.source.connect(node);
      node.connect(context.destination);
      this.cueBuffer = context.createBuffer(1, Math.round(context.sampleRate * 0.12), context.sampleRate);
      const samples = this.cueBuffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) {
        samples[i] = 0.04 * Math.sin(2 * Math.PI * 660 * i / context.sampleRate)
          * Math.sin(Math.PI * i / samples.length);
      }
    } catch (error) {
      this.close();
      throw error;
    }
  }

  reset(epoch: number): void { this.artifacts.cancel(); this.node?.port.postMessage({ type: "reset", epoch }); }
  playArtifact(epoch: number, data: ArrayBuffer): Promise<void> { return this.artifacts.play(epoch, data); }
  finish(epoch: number): void { this.node?.port.postMessage({ type: "finish", epoch }); }
  push(epoch: number, pcm: Int16Array): void {
    this.node?.port.postMessage({ type: "audio", epoch, pcm }, [pcm.buffer]);
  }

  reflexPlayer(): LocalReflexClipPlayer {
    return {
      isReady: () => this.cueBuffer !== null && !this.disposed,
      play: (_cue, signal) => this.playCue(signal),
      stop: () => this.stopCue(),
    };
  }

  private playCue(signal: AbortSignal): Promise<void> {
    this.stopCue();
    if (!this.context || !this.cueBuffer || signal.aborted) return Promise.resolve();
    const source = this.context.createBufferSource();
    source.buffer = this.cueBuffer;
    source.connect(this.context.destination);
    this.cue = source;
    return new Promise((resolve) => {
      const finish = () => {
        signal.removeEventListener("abort", stop);
        source.disconnect();
        if (this.cue === source) { this.cue = null; this.cueDone = null; }
        resolve();
      };
      const stop = () => this.stopCue();
      this.cueDone = finish;
      source.onended = finish;
      signal.addEventListener("abort", stop, { once: true });
      source.start();
    });
  }

  private stopCue(): void {
    this.cue?.stop();
    this.cueDone?.();
  }

  close(): void {
    this.disposed = true;
    this.artifacts.cancel();
    this.stopCue();
    this.cueBuffer = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.source?.disconnect();
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
      this.node.port.close();
    }
    if (this.context && this.context.state !== "closed") void this.context.close().catch(() => {});
    this.stream = null;
    this.source = null;
    this.node = null;
    this.context = null;
  }
}
