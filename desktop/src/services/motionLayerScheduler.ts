import type {
  MotionLayer,
  MotionLayerSource,
  MotionMixer,
} from "@/domain/motionRuntime";
import type { NormalizedMotionClip } from "@/domain/normalizedPose";
import type { Live2DRenderer } from "@/renderers/live2dRenderer";

/** Keeps loopable idle motion alive beneath short-lived higher-priority layers. */
export class MotionLayerScheduler {
  private readonly layers = new Map<MotionLayerSource, MotionLayer & { startedAt: number }>();
  private readonly timers = new Map<
    MotionLayerSource,
    ReturnType<typeof setTimeout>
  >();
  private readonly mixer: MotionMixer;
  private readonly renderer: Live2DRenderer;
  private sequence = 0;
  private readonly now: () => number;

  constructor(
    mixer: MotionMixer,
    renderer: Live2DRenderer,
    now: () => number = () => performance.now(),
  ) {
    this.mixer = mixer;
    this.renderer = renderer;
    this.now = now;
  }

  play(
    intentId: string,
    source: MotionLayerSource,
    clip: NormalizedMotionClip,
    elapsedMs = 0,
  ): boolean {
    if (!clip.loopable && elapsedMs >= clip.durationMs) return false;
    const now = this.now();
    const layer = {
      id: `${intentId}:layer`,
      source,
      sequence: ++this.sequence,
      clip,
      startedAt: now - elapsedMs,
    };
    this.layers.set(source, layer);
    this.scheduleExpiry(layer, elapsedMs);
    return this.render(now);
  }

  clear(): void {
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
    this.layers.clear();
  }

  private scheduleExpiry(layer: MotionLayer, elapsedMs: number): void {
    const previousTimer = this.timers.get(layer.source);
    if (previousTimer) clearTimeout(previousTimer);
    this.timers.delete(layer.source);
    if (layer.source === "idle" && layer.clip.loopable) return;
    const timer = setTimeout(() => {
      this.timers.delete(layer.source);
      if (this.layers.get(layer.source)?.id !== layer.id) return;
      this.layers.delete(layer.source);
      this.render();
    }, Math.max(0, layer.clip.durationMs - elapsedMs));
    this.timers.set(layer.source, timer);
  }

  private render(now = this.now()): boolean {
    const mixed = this.mixer.mix([...this.layers.values()].map((layer) => ({
      ...layer,
      elapsedMs: Math.max(0, now - layer.startedAt),
    })));
    if (mixed) return this.renderer.playNormalizedMotion(mixed);
    this.renderer.clearRuntimeMotion();
    return false;
  }
}
