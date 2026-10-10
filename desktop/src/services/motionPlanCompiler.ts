import type { MotionPlan, MotionPlanSegment } from "@/domain/motionPlan";
import {
  generateMotionPrimitive,
  isMotionPrimitiveName,
} from "@/domain/motionPrimitives";
import {
  createNormalizedPoseFrame,
  neutralNormalizedPose,
  normalizedPoseChannels,
  type NormalizedMotionClip,
  type NormalizedPoseChannel,
  type NormalizedPoseValues,
} from "@/domain/normalizedPose";

export interface CompileMotionPlanOptions {
  previousPose?: Partial<NormalizedPoseValues>;
}

interface TimelineEvent {
  atMs: number;
  channels: NormalizedPoseChannel[];
  values: NormalizedPoseValues;
}

function valueAt(
  track: TimelineEvent[],
  channel: NormalizedPoseChannel,
  atMs: number,
  fallback: number,
): number {
  let left = 0;
  let right = track.length;
  while (left < right) {
    const middle = Math.floor((left + right) / 2);
    if (track[middle]!.atMs <= atMs) left = middle + 1;
    else right = middle;
  }
  const previous = track[left - 1];
  const next = track[left];
  const previousValue = previous?.values[channel] ?? fallback;
  if (!next) return previousValue;
  const previousTime = previous?.atMs ?? 0;
  const progress = Math.max(0, (atMs - previousTime) / (next.atMs - previousTime));
  return previousValue + (next.values[channel] - previousValue) * progress;
}

function channelTracks(events: TimelineEvent[]): Map<NormalizedPoseChannel, TimelineEvent[]> {
  const tracks = new Map<NormalizedPoseChannel, TimelineEvent[]>();
  for (const event of events) {
    for (const channel of event.channels) {
      if (!tracks.has(channel)) tracks.set(channel, []);
      tracks.get(channel)!.push(event);
    }
  }
  for (const track of tracks.values()) track.sort((left, right) => left.atMs - right.atMs);
  return tracks;
}

function numberParam(
  segment: Extract<MotionPlanSegment, { type: "primitive" }>,
  name: string,
): number | undefined {
  const value = segment.params[name];
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

function booleanParam(
  segment: Extract<MotionPlanSegment, { type: "primitive" }>,
  name: string,
): boolean | undefined {
  const value = segment.params[name];
  return typeof value === "boolean" ? value : undefined;
}

function segmentEvents(
  plan: MotionPlan,
  segment: MotionPlanSegment,
  startPose: NormalizedPoseValues,
): TimelineEvent[] {
  if (segment.type === "expression") return [];
  if (segment.type === "primitive") {
    if (!isMotionPrimitiveName(segment.name)) return [];
    const clip = generateMotionPrimitive(segment.name, {
      clipId: `${plan.id}:segment:${segment.atMs}:${segment.name}`,
      intentId: plan.intent.id,
      durationMs: segment.durationMs,
      intensity: numberParam(segment, "intensity"),
      repeat: numberParam(segment, "repeat"),
      startPose,
      loopable: booleanParam(segment, "loopable"),
    });
    return clip.frames.map((frame) => ({
      atMs: segment.atMs + frame.atMs,
      channels: clip.channels,
      values: frame,
    }));
  }
  return [
    { atMs: segment.atMs, channels: [...normalizedPoseChannels], values: startPose },
    ...segment.keyframes.map((frame) => ({
      atMs: segment.atMs + Math.max(0, Math.min(segment.durationMs, frame.atMs)),
      channels: [...normalizedPoseChannels],
      values: frame,
    })),
  ];
}

/** Compiles supported MotionPlan segments into the normalized renderer boundary. */
export function compileMotionPlan(
  plan: MotionPlan,
  options: CompileMotionPlanOptions = {},
): NormalizedMotionClip | null {
  const initialPose: NormalizedPoseValues = {
    ...neutralNormalizedPose,
    ...options.previousPose,
  };
  const events: TimelineEvent[] = [];
  for (const segment of [...plan.segments].sort(
    (left, right) => left.atMs - right.atMs,
  )) {
    if (segment.type === "expression") continue;
    const tracks = channelTracks(events);
    const segmentStartPose = { ...initialPose };
    for (const channel of normalizedPoseChannels) {
      segmentStartPose[channel] = valueAt(tracks.get(channel) ?? [], channel, segment.atMs, initialPose[channel]);
    }
    const generated = segmentEvents(plan, segment, segmentStartPose);
    events.push(...generated);
  }
  if (!events.length) return null;

  events.sort((left, right) => left.atMs - right.atMs);
  const durationMs = Math.max(
    plan.durationMs,
    ...events.map((event) => event.atMs),
  );
  const tracks = channelTracks(events);
  const channels = normalizedPoseChannels.filter((channel) =>
    events.some((event) => event.channels.includes(channel)),
  );
  const times = new Set([0, durationMs, ...events.map((event) => event.atMs)]);
  const frames = [...times].sort((left, right) => left - right).map((atMs) => {
    const pose = { ...initialPose };
    for (const channel of channels) {
      pose[channel] = valueAt(tracks.get(channel) ?? [], channel, atMs, initialPose[channel]);
    }
    return createNormalizedPoseFrame(atMs, pose);
  });

  return {
    id: `${plan.id}:compiled`,
    intentId: plan.intent.id,
    durationMs,
    loopable: plan.intent.loopable,
    restoreAtEnd: true,
    channels,
    frames,
  };
}
