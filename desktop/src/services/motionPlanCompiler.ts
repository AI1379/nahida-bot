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
  return segment.keyframes.map((frame) => ({
    atMs: segment.atMs + Math.max(0, Math.min(segment.durationMs, frame.atMs)),
    channels: [...normalizedPoseChannels],
    values: frame,
  }));
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
  let segmentStartPose = { ...initialPose };
  for (const segment of [...plan.segments].sort(
    (left, right) => left.atMs - right.atMs,
  )) {
    const generated = segmentEvents(plan, segment, segmentStartPose);
    events.push(...generated);
    const finalEvent = generated.at(-1);
    if (finalEvent) {
      for (const channel of finalEvent.channels) {
        segmentStartPose[channel] = finalEvent.values[channel];
      }
    }
  }
  if (!events.length) return null;

  events.sort((left, right) => left.atMs - right.atMs);
  const durationMs = Math.max(
    plan.durationMs,
    ...events.map((event) => event.atMs),
  );
  const channels = normalizedPoseChannels.filter((channel) =>
    events.some((event) => event.channels.includes(channel)),
  );
  const eventsByTime = new Map<number, TimelineEvent[]>();
  for (const event of events) {
    const atMs = Math.max(0, Math.min(durationMs, event.atMs));
    eventsByTime.set(atMs, [...(eventsByTime.get(atMs) ?? []), event]);
  }
  eventsByTime.set(0, eventsByTime.get(0) ?? []);
  eventsByTime.set(durationMs, eventsByTime.get(durationMs) ?? []);

  const pose = { ...initialPose };
  const frames = [...eventsByTime.entries()]
    .sort(([left], [right]) => left - right)
    .map(([atMs, atMsEvents]) => {
      for (const event of atMsEvents) {
        for (const channel of event.channels) {
          pose[channel] = event.values[channel];
        }
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
