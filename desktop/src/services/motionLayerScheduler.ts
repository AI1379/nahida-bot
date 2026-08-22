import type {
  MotionLayer,
  MotionLayerSource,
  MotionMixer,
} from "@/domain/motionRuntime";
import type { NormalizedMotionClip } from "@/domain/normalizedPose";
import type { Live2DRenderer } from "@/renderers/live2dRenderer";

/** Keeps loopable idle motion alive beneath short-lived higher-priority layers. */
export class MotionLayerScheduler {
  private readonly layers = new Map<MotionLayerSource, MotionLayer>();
  private readonly timers = new Map<
    MotionLayerSource,
    ReturnType<typeof setTimeout>
  >();
  private readonly mixer: MotionMixer;
  private readonly renderer: Live2DRenderer;
  private sequence = 0;

  constructor(
    mixer: MotionMixer,
    renderer: Live2DRenderer,
  ) {
    this.mixer = mixer;
    this.renderer = renderer;
  }

  play(
    intentId: string,
    source: MotionLayerSource,
    clip: NormalizedMotionClip,
  ): boolean {
    const layer: MotionLayer = {
      id: `${intentId}:layer`,
      source,
      sequence: ++this.sequence,
      clip,
    };
    this.layers.set(source, layer);
    this.scheduleExpiry(layer);
    return this.render();
  }

  clear(): void {
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
    this.layers.clear();
  }

  private scheduleExpiry(layer: MotionLayer): void {
    const previousTimer = this.timers.get(layer.source);
    if (previousTimer) clearTimeout(previousTimer);
    this.timers.delete(layer.source);
    if (layer.source === "idle" && layer.clip.loopable) return;
    const timer = setTimeout(() => {
      this.timers.delete(layer.source);
      if (this.layers.get(layer.source)?.id !== layer.id) return;
      this.layers.delete(layer.source);
      this.render();
    }, Math.max(0, layer.clip.durationMs));
    this.timers.set(layer.source, timer);
  }

  private render(): boolean {
    const mixed = this.mixer.mix([...this.layers.values()]);
    if (mixed) return this.renderer.playNormalizedMotion(mixed);
    this.renderer.clearRuntimeMotion();
    return false;
  }
}
