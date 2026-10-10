/* PCM capture/resampling and bounded playback. Loaded directly by AudioWorklet. */
class VoiceLabProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ring = new Float32Array(12000); // At most 500ms of 24kHz audio.
    this.read = 0;
    this.write = 0;
    this.count = 0;
    this.phase = 0;
    this.epoch = 0;
    this.started = false;
    this.finished = false;
    this.underrun = false;
    this.frame = new Int16Array(320);
    this.frameIndex = 0;
    this.captureSum = 0;
    this.captureWeight = 0;
    this.energy = 0;
    this.port.onmessage = ({ data }) => this.command(data);
  }

  command(data) {
    if (data.type === "reset") {
      this.epoch = data.epoch;
      this.read = this.write = this.count = this.phase = 0;
      this.started = this.finished = false;
      this.underrun = false;
      this.port.postMessage({ type: "stopped", epoch: this.epoch });
      return;
    }
    if (data.epoch !== this.epoch) return;
    if (data.type === "finish") {
      this.finished = true;
      return;
    }
    if (data.type !== "audio") return;
    const pcm = data.pcm;
    if (this.count + pcm.length > this.ring.length) {
      // Never let a slow consumer accumulate an unbounded speaking delay.
      this.port.postMessage({ type: "overflow", epoch: this.epoch });
      return;
    }
    for (const value of pcm) {
      this.ring[this.write] = value / 32768;
      this.write = (this.write + 1) % this.ring.length;
    }
    this.count += pcm.length;
  }

  capture(input) {
    const ratio = sampleRate / 16000;
    for (const sample of input) {
      let remaining = 1;
      while (remaining > 1e-9) {
        const weight = Math.min(remaining, ratio - this.captureWeight);
        this.captureSum += sample * weight;
        this.captureWeight += weight;
        remaining -= weight;
        if (this.captureWeight < ratio - 1e-9) continue;
        const value = Math.max(-1, Math.min(1, this.captureSum / ratio));
        this.frame[this.frameIndex++] = Math.round(value * 32767);
        this.energy += value * value;
        this.captureSum = this.captureWeight = 0;
        if (this.frameIndex !== 320) continue;
        this.port.postMessage({
          type: "capture", pcm: this.frame.buffer, rms: Math.sqrt(this.energy / 320),
          queued_ms: this.count / 24,
        }, [this.frame.buffer]);
        this.frame = new Int16Array(320);
        this.frameIndex = this.energy = 0;
      }
    }
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    if (input) this.capture(input);
    const output = outputs[0]?.[0];
    if (!output) return true;
    if (!this.started && this.count < 1440 && !this.finished) {
      output.fill(0); // 60ms initial jitter buffer.
      return true;
    }
    if (this.count > 0 && !this.started) {
      this.started = true;
      this.port.postMessage({ type: "started", epoch: this.epoch });
    }
    if (this.count > 0) this.underrun = false;
    for (let i = 0; i < output.length; i++) {
      if (this.count === 0) {
        output[i] = 0;
        continue;
      }
      const first = this.ring[this.read];
      const next = this.ring[(this.read + 1) % this.ring.length];
      output[i] = first + ((this.count > 1 ? next : first) - first) * this.phase;
      this.phase += 24000 / sampleRate;
      const advance = Math.min(Math.floor(this.phase), this.count);
      this.phase -= advance;
      this.read = (this.read + advance) % this.ring.length;
      this.count -= advance;
      if (this.count === 0) this.phase = 0;
    }
    if (this.finished && this.count === 0) {
      this.finished = false;
      this.started = false;
      this.port.postMessage({ type: "done", epoch: this.epoch });
    } else if (this.started && this.count === 0 && !this.underrun) {
      this.underrun = true;
      this.port.postMessage({ type: "underrun", epoch: this.epoch });
    }
    return true;
  }
}

registerProcessor("nahida-voice-lab", VoiceLabProcessor);
