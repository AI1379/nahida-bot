import { presentationTimingDefaults } from "@/config/desktopRuntimeDefaults";
import type {
  DisplayEmotion,
  DisplayMotion,
  DisplayPlan,
  DisplaySegment,
} from "@/domain/displayPlan";
import type {
  MotionCommunicativeAct,
  MotionEmotion,
  MotionGaze,
  MotionIntent,
  MotionIntentName,
  MotionIntentSource,
  MotionPriority,
} from "@/domain/motionIntent";
import {
  displayMotionPrimitiveMap,
  motionPrimitiveDefaultDurationMs,
} from "@/domain/motionPrimitives";

interface MotionIntentTemplate {
  intent: MotionIntentName;
  intensity: number;
  gaze?: MotionGaze;
  communicativeAct?: MotionCommunicativeAct;
  loopable: boolean;
  interruptible: boolean;
  priority: MotionPriority;
}

export interface DisplaySegmentMotionIntent {
  segmentIndex: number;
  totalSegments: number;
  displayMotion: DisplayMotion;
  intent: MotionIntent;
}

export interface DisplaySegmentMotionAdapterOptions {
  presentationId: string;
  segmentIndex: number;
  totalSegments: number;
  speaking: boolean;
  durationMs?: number;
  source?: MotionIntentSource;
}

export interface DisplayPlanMotionAdapterOptions {
  presentationId: string;
  speaking: boolean | ((segment: DisplaySegment, index: number) => boolean);
  durationMs?: number | ((segment: DisplaySegment, index: number) => number);
  source?: MotionIntentSource;
}

const explicitMotionTemplates: Partial<
  Record<DisplayMotion, MotionIntentTemplate>
> = {
  nod: {
    intent: "agree",
    intensity: 0.45,
    gaze: "user",
    communicativeAct: "confirm",
    loopable: false,
    interruptible: true,
    priority: "speech",
  },
  point: {
    intent: "explain",
    intensity: 0.5,
    gaze: "user",
    communicativeAct: "answer",
    loopable: false,
    interruptible: true,
    priority: "speech",
  },
  wave: {
    intent: "greet",
    intensity: 0.6,
    gaze: "user",
    loopable: false,
    interruptible: true,
    priority: "speech",
  },
  notify: {
    intent: "surprised",
    intensity: 0.65,
    gaze: "user",
    communicativeAct: "warn",
    loopable: false,
    interruptible: true,
    priority: "speech",
  },
  emerge: {
    intent: "emerge",
    intensity: 0.65,
    gaze: "user",
    loopable: false,
    interruptible: false,
    priority: "state-transition",
  },
  retreat: {
    intent: "retreat",
    intensity: 0.55,
    gaze: "side",
    loopable: false,
    interruptible: false,
    priority: "state-transition",
  },
};

const emotionTemplates: Partial<Record<DisplayEmotion, MotionIntentTemplate>> = {
  thinking: {
    intent: "thinking",
    intensity: 0.32,
    gaze: "down-left",
    communicativeAct: "search",
    loopable: true,
    interruptible: true,
    priority: "speech",
  },
  worried: {
    intent: "concerned",
    intensity: 0.35,
    gaze: "user",
    communicativeAct: "comfort",
    loopable: false,
    interruptible: true,
    priority: "speech",
  },
  error: {
    intent: "error",
    intensity: 0.45,
    gaze: "user",
    communicativeAct: "warn",
    loopable: false,
    interruptible: true,
    priority: "critical",
  },
  offline: {
    intent: "error",
    intensity: 0.25,
    gaze: "none",
    loopable: true,
    interruptible: true,
    priority: "critical",
  },
};

const idleTemplate: MotionIntentTemplate = {
  intent: "idle",
  intensity: 0.12,
  gaze: "user",
  loopable: true,
  interruptible: true,
  priority: "background",
};

const speakingTemplate: MotionIntentTemplate = {
  intent: "explain",
  intensity: 0.35,
  gaze: "user",
  communicativeAct: "answer",
  loopable: false,
  interruptible: true,
  priority: "speech",
};

function motionEmotion(emotion: DisplayEmotion | undefined): MotionEmotion {
  return emotion ?? "neutral";
}

function legacyDisplayMotion(
  segment: DisplaySegment,
  speaking: boolean,
): DisplayMotion {
  return segment.motion ?? (speaking ? "speaking" : "idle");
}

function intentTemplate(
  segment: DisplaySegment,
  displayMotion: DisplayMotion,
  speaking: boolean,
): MotionIntentTemplate {
  return (
    explicitMotionTemplates[displayMotion] ??
    emotionTemplates[segment.emotion ?? "neutral"] ??
    (speaking ? speakingTemplate : idleTemplate)
  );
}

function estimatedDurationMs(
  segment: DisplaySegment,
  displayMotion: DisplayMotion,
): number {
  if (displayMotion !== "idle" && displayMotion !== "speaking") {
    return motionPrimitiveDefaultDurationMs(
      displayMotionPrimitiveMap[displayMotion],
    );
  }

  const speed = segment.voice?.speed ?? 1;
  return Math.max(
    presentationTimingDefaults.minimumSegmentDurationMs,
    (segment.text.length * presentationTimingDefaults.millisecondsPerCharacter) /
      speed,
  );
}

function finitePositiveDuration(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : fallback;
}

export function adaptDisplaySegmentToMotionIntent(
  segment: DisplaySegment,
  options: DisplaySegmentMotionAdapterOptions,
): DisplaySegmentMotionIntent {
  const displayMotion = legacyDisplayMotion(segment, options.speaking);
  const template = intentTemplate(segment, displayMotion, options.speaking);
  const durationMs = finitePositiveDuration(
    options.durationMs,
    estimatedDurationMs(segment, displayMotion),
  );

  return {
    segmentIndex: options.segmentIndex,
    totalSegments: options.totalSegments,
    displayMotion,
    intent: {
      id: `${options.presentationId}:segment:${options.segmentIndex}`,
      source: options.source ?? "rule",
      intent: template.intent,
      emotion: motionEmotion(segment.emotion),
      communicativeAct: template.communicativeAct,
      durationMs,
      intensity: template.intensity,
      gaze: template.gaze,
      loopable: template.loopable,
      interruptible: template.interruptible,
      priority: template.priority,
      tags: ["display-plan", `display-motion:${displayMotion}`],
    },
  };
}

function optionValue<T>(
  value: T | ((segment: DisplaySegment, index: number) => T) | undefined,
  segment: DisplaySegment,
  index: number,
  fallback: T,
): T {
  if (typeof value === "function") {
    return (value as (segment: DisplaySegment, index: number) => T)(
      segment,
      index,
    );
  }
  return value ?? fallback;
}

export function adaptDisplayPlanToMotionIntents(
  plan: DisplayPlan,
  options: DisplayPlanMotionAdapterOptions,
): DisplaySegmentMotionIntent[] {
  return plan.segments.map((segment, index) => {
    const speaking = optionValue(options.speaking, segment, index, false);
    const displayMotion = legacyDisplayMotion(segment, speaking);
    return adaptDisplaySegmentToMotionIntent(segment, {
      presentationId: options.presentationId,
      segmentIndex: index,
      totalSegments: plan.segments.length,
      speaking,
      durationMs: optionValue(
        options.durationMs,
        segment,
        index,
        estimatedDurationMs(segment, displayMotion),
      ),
      source: options.source,
    });
  });
}
