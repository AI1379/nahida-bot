import type { MotionDriver, MotionDriverInput, MotionDriverResult } from "@/domain/motionDriver";
import {
  generateMotionPrimitive,
  isMotionPrimitiveName,
  motionPrimitiveDefaultDurationMs,
  type MotionPrimitiveName,
} from "@/domain/motionPrimitives";
import { intentPrimitiveMap } from "./primitiveMotionSynthesizer";

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function primitiveForInput(input: MotionDriverInput): MotionPrimitiveName {
  return isMotionPrimitiveName(input.context.motionHint)
    ? input.context.motionHint
    : intentPrimitiveMap[input.intent.intent];
}

function repeatForInput(
  input: MotionDriverInput,
  primitive: MotionPrimitiveName,
): number {
  if (!input.intent.loopable || input.phase === "exit") return 1;
  return clamp(
    Math.round(
      input.intent.durationMs / motionPrimitiveDefaultDurationMs(primitive),
    ),
    1,
    8,
  );
}

function intensityForInput(
  input: MotionDriverInput,
  primitive: MotionPrimitiveName,
): number {
  const speechEnergy = primitive === "speaking" ? input.audioEnergy * 0.25 : 0;
  const phaseScale = input.phase === "exit" ? 0.6 : 1;
  return clamp((input.intent.intensity + speechEnergy) * phaseScale, 0, 1);
}

export class RuleMotionDriver implements MotionDriver {
  readonly id = "rule-motion-driver";
  readonly version = "1.0.0";

  async drive(input: MotionDriverInput): Promise<MotionDriverResult> {
    const primitive = primitiveForInput(input);
    return {
      primitive,
      clip: generateMotionPrimitive(primitive, {
        clipId: `${input.intent.id}:${primitive}`,
        intentId: input.intent.id,
        durationMs: input.intent.durationMs,
        intensity: intensityForInput(input, primitive),
        repeat: repeatForInput(input, primitive),
        startPose: input.previousPose,
        loopable: input.intent.loopable && input.phase !== "exit",
      }),
      warnings: [],
    };
  }
}
