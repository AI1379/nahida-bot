export interface AudioEnergyEnvelopeOptions {
  attackMs: number;
  releaseMs: number;
  noiseFloor: number;
  gain: number;
}

export const defaultAudioEnergyEnvelopeOptions: AudioEnergyEnvelopeOptions = {
  attackMs: 45,
  releaseMs: 140,
  noiseFloor: 0.015,
  gain: 3.2,
};

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

export function audioRmsEnergy(
  samples: Uint8Array,
  options = defaultAudioEnergyEnvelopeOptions,
): number {
  if (!samples.length) return 0;
  let sumSquares = 0;
  for (const sample of samples) {
    const normalized = (sample - 128) / 128;
    sumSquares += normalized * normalized;
  }
  const rms = Math.sqrt(sumSquares / samples.length);
  return clamp((rms - options.noiseFloor) * options.gain, 0, 1);
}

export function smoothAudioEnergy(
  previous: number,
  target: number,
  deltaMs: number,
  options = defaultAudioEnergyEnvelopeOptions,
): number {
  const timeConstant = target > previous ? options.attackMs : options.releaseMs;
  const alpha = 1 - Math.exp(-Math.max(deltaMs, 0) / Math.max(timeConstant, 1));
  return clamp(previous + (target - previous) * alpha, 0, 1);
}

export type AudioEnergyListener = (energy: number) => void;

export class MediaElementEnergyMonitor {
  private readonly analyser: AnalyserNode;
  private readonly audioContext: AudioContext;
  private readonly listener: AudioEnergyListener;
  private readonly samples: Uint8Array<ArrayBuffer>;
  private frameHandle: number | null = null;
  private lastSampleAt = 0;
  private lastPublishedAt = 0;
  private energy = 0;

  constructor(
    audio: HTMLAudioElement,
    listener: AudioEnergyListener,
    createAudioContext: () => AudioContext = () => new AudioContext(),
  ) {
    this.listener = listener;
    this.audioContext = createAudioContext();
    const source = this.audioContext.createMediaElementSource(audio);
    this.analyser = this.audioContext.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0;
    source.connect(this.analyser);
    this.analyser.connect(this.audioContext.destination);
    this.samples = new Uint8Array(new ArrayBuffer(this.analyser.fftSize));
  }

  async start(): Promise<void> {
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume();
    }
    if (this.frameHandle !== null) return;
    this.lastSampleAt = performance.now();
    this.lastPublishedAt = this.lastSampleAt;
    this.frameHandle = requestAnimationFrame(this.sample);
  }

  stop(): void {
    if (this.frameHandle !== null) cancelAnimationFrame(this.frameHandle);
    this.frameHandle = null;
    this.energy = 0;
    this.listener(0);
  }

  async dispose(): Promise<void> {
    this.stop();
    await this.audioContext.close();
  }

  private readonly sample = (now: number): void => {
    this.analyser.getByteTimeDomainData(this.samples);
    const target = audioRmsEnergy(this.samples);
    this.energy = smoothAudioEnergy(this.energy, target, now - this.lastSampleAt);
    this.lastSampleAt = now;
    if (now - this.lastPublishedAt >= 1000 / 30) {
      this.listener(this.energy);
      this.lastPublishedAt = now;
    }
    this.frameHandle = requestAnimationFrame(this.sample);
  };
}
