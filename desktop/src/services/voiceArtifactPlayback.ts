/** Decode a complete TTS artifact, then feed bounded 20ms PCM to the worklet. */
interface ArtifactOutput {
  decode(data: ArrayBuffer): Promise<AudioBuffer>;
  push(epoch: number, pcm: Int16Array): void;
  finish(epoch: number): void;
}

export class VoiceArtifactPlayback {
  private generation = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly output: ArtifactOutput;
  constructor(output: ArtifactOutput) { this.output = output; }

  cancel(): void {
    this.generation++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  async play(epoch: number, data: ArrayBuffer): Promise<void> {
    this.cancel();
    const generation = this.generation;
    const audio = await this.output.decode(data);
    if (generation !== this.generation) return;
    if (audio.duration <= 0 || audio.duration > 60 || audio.numberOfChannels > 2) {
      throw new Error("TTS 音频必须为 60 秒以内的单声道或双声道音频");
    }
    const channels = Array.from({ length: audio.numberOfChannels }, (_, i) => audio.getChannelData(i));
    const count = Math.ceil(audio.duration * 24000);
    let offset = 0;
    const pump = () => {
      if (generation !== this.generation) return;
      const pcm = new Int16Array(480);
      for (let i = 0; i < 480 && offset < count; i++, offset++) {
        const position = offset * audio.sampleRate / 24000;
        const left = Math.min(Math.floor(position), audio.length - 1);
        const right = Math.min(left + 1, audio.length - 1);
        const fraction = position - left;
        let sample = 0;
        for (const channel of channels) sample += channel[left]! * (1 - fraction) + channel[right]! * fraction;
        pcm[i] = Math.round(Math.max(-1, Math.min(1, sample / channels.length)) * 32767);
      }
      this.output.push(epoch, pcm);
      if (offset < count) this.timer = setTimeout(pump, 20);
      else { this.timer = null; this.output.finish(epoch); }
    };
    pump();
  }
}
