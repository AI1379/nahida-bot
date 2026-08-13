import type { MotionIntent, MotionIntentName } from "@/domain/motionIntent";
import type { MotionPlan } from "@/domain/motionPlan";
import type {
  MotionSynthesizer,
  MotionSynthesisContext,
} from "@/domain/motionRuntime";
import {
  motionPrimitiveDefaultDurationMs,
  type MotionPrimitiveName,
} from "@/domain/motionPrimitives";

export const intentPrimitiveMap: Readonly<
  Record<MotionIntentName, MotionPrimitiveName>
> = {
  idle: "idle-breathe",
  greet: "wave",
  thinking: "think-loop",
  explain: "explain-small",
  agree: "nod",
  deny: "shake",
  surprised: "surprised-pop",
  concerned: "sad-drop",
  celebrate: "celebrate",
  apology: "sad-drop",
  error: "notify",
  retreat: "retreat",
  emerge: "emerge",
};

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

export class PrimitiveMotionSynthesizer implements MotionSynthesizer {
  readonly id = "primitive-motion-synthesizer";
  readonly version = "1.0.0";

  async synthesize(
    intent: Parameters<MotionSynthesizer["synthesize"]>[0],
    context: MotionSynthesisContext,
  ): Promise<MotionPlan> {
    return createPrimitiveMotionPlan(intent, context);
  }
}

export function createPrimitiveMotionPlan(
  intent: MotionIntent,
  context: MotionSynthesisContext,
  primitive: MotionPrimitiveName = intentPrimitiveMap[intent.intent],
): MotionPlan {
  const repeat = intent.loopable
    ? clamp(
        Math.round(
          intent.durationMs / motionPrimitiveDefaultDurationMs(primitive),
        ),
        1,
        8,
      )
    : 1;
  return {
    schemaVersion: 1,
    id: `${intent.id}:plan:${primitive}`,
    intent,
    createdAt: new Date().toISOString(),
    durationMs: intent.durationMs,
    segments: [
      {
        type: "primitive",
        name: primitive,
        atMs: 0,
        durationMs: intent.durationMs,
        params: {
          intensity: clamp(intent.intensity, 0, 1),
          repeat,
          loopable: intent.loopable,
          audioEnergy: clamp(context.audioEnergy, 0, 1),
          profileVersion: context.modelProfile.profileVersion,
        },
      },
    ],
    validationWarnings: [],
    telemetryId: intent.id,
  };
}
