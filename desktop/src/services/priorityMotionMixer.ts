import type {
  MotionLayer,
  MotionLayerSource,
  MotionMixer,
} from "@/domain/motionRuntime";
import {
  createNormalizedPoseFrame,
  neutralNormalizedPose,
  normalizedPoseChannels,
  type NormalizedMotionClip,
  type NormalizedPoseChannel,
  type NormalizedPoseValues,
} from "@/domain/normalizedPose";

const sourcePriority: Record<MotionLayerSource, number> = {
  debug: 700,
  "state-transition": 600,
  safety: 500,
  "lip-sync": 400,
  speech: 300,
  expression: 200,
  idle: 100,
};

function layerOrder(left: MotionLayer, right: MotionLayer): number {
  return (
    sourcePriority[right.source] - sourcePriority[left.source] ||
    right.sequence - left.sequence
  );
}

function selectedLayersByChannel(
  layers: MotionLayer[],
): Map<NormalizedPoseChannel, MotionLayer> {
  const selected = new Map<NormalizedPoseChannel, MotionLayer>();
  for (const layer of [...layers].sort(layerOrder)) {
    for (const channel of layer.clip.channels) {
      if (!selected.has(channel)) selected.set(channel, layer);
    }
  }
  return selected;
}

function clipTime(clip: NormalizedMotionClip, atMs: number): number {
  if (clip.loopable && clip.durationMs > 0) return atMs % clip.durationMs;
  return Math.min(atMs, clip.durationMs);
}

function sampleChannel(
  clip: NormalizedMotionClip,
  channel: NormalizedPoseChannel,
  atMs: number,
): number {
  const time = clipTime(clip, atMs);
  const frames = clip.frames;
  if (!frames.length) return neutralNormalizedPose[channel];
  let previous = frames[0]!;
  let next = frames.at(-1)!;
  for (let index = 1; index < frames.length; index += 1) {
    next = frames[index]!;
    if (time <= next.atMs) break;
    previous = next;
  }
  const duration = next.atMs - previous.atMs;
  if (duration <= 0) return next[channel];
  const progress = Math.max(0, Math.min(1, (time - previous.atMs) / duration));
  return previous[channel] + (next[channel] - previous[channel]) * progress;
}

function mixerTimeline(
  selected: Map<NormalizedPoseChannel, MotionLayer>,
  durationMs: number,
): number[] {
  const times = new Set<number>([0, durationMs]);
  for (const layer of new Set(selected.values())) {
    for (const frame of layer.clip.frames) {
      if (frame.atMs >= 0 && frame.atMs <= durationMs) times.add(frame.atMs);
    }
  }
  return [...times].sort((left, right) => left - right);
}

export class PriorityMotionMixer implements MotionMixer {
  readonly id = "priority-motion-mixer";
  readonly version = "1.0.0";

  mix(layers: MotionLayer[]): NormalizedMotionClip | null {
    const usable = layers.filter(
      (layer) => layer.clip.channels.length && layer.clip.frames.length,
    );
    if (!usable.length) return null;

    const selected = selectedLayersByChannel(usable);
    const channels = normalizedPoseChannels.filter((channel) =>
      selected.has(channel),
    );
    const selectedLayers = [...new Set(selected.values())];
    const durationMs = Math.max(
      ...selectedLayers.map((layer) => layer.clip.durationMs),
    );
    const frames = mixerTimeline(selected, durationMs).map((atMs) => {
      const values: NormalizedPoseValues = { ...neutralNormalizedPose };
      for (const channel of channels) {
        const layer = selected.get(channel);
        if (layer) values[channel] = sampleChannel(layer.clip, channel, atMs);
      }
      return createNormalizedPoseFrame(atMs, values);
    });
    const highestLayer = [...usable].sort(layerOrder)[0]!;

    return {
      id: `mix:${selectedLayers.map((layer) => layer.clip.id).join("+")}`,
      intentId: highestLayer.clip.intentId,
      durationMs,
      loopable: selectedLayers.every((layer) => layer.clip.loopable),
      restoreAtEnd: selectedLayers.every(
        (layer) => layer.clip.restoreAtEnd,
      ),
      channels,
      frames,
    };
  }
}
