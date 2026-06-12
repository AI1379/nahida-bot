import { describe, expect, it } from "vitest";

import type { Live2DModelManifest } from "@/domain/live2d";
import type {
  Live2DRenderer,
} from "@/renderers/live2dRenderer";
import { Live2DPresentationController } from "./live2dPresentationController";

class FakeRenderer implements Live2DRenderer {
  readonly expressions: string[] = [];
  readonly modelMotions: Array<[string, number]> = [];
  readonly baseMotions: string[] = [];
  modelMotionResult = false;
  successfulExpression = "";

  async loadModel(): Promise<void> {}
  updateModelConfig(): void {}

  async applyExpression(expressionName: string): Promise<boolean> {
    this.expressions.push(expressionName);
    return expressionName === this.successfulExpression;
  }

  async playModelMotion(group: string, index: number): Promise<boolean> {
    this.modelMotions.push([group, index]);
    return this.modelMotionResult;
  }

  playBaseMotion(motion: Parameters<Live2DRenderer["playBaseMotion"]>[0]): boolean {
    this.baseMotions.push(motion);
    return true;
  }

  clearRuntimeMotion(): void {}
  setLipSync(): void {}
  setFpsMode(): void {}
  dispose(): void {}
}

const manifest: Live2DModelManifest = {
  id: "test-model",
  name: "Test Model",
  entry: "/test.model3.json",
  source: "bundled",
  emotionMap: {
    custom: ["missing-custom"],
    happy: ["missing-happy"],
    neutral: ["neutral-expression"],
  },
  motionMap: {
    nod: { source: "model", group: "Gesture", index: 1 },
  },
  lipSync: { enabled: true, parameterIds: ["ParamMouthOpenY"] },
  layout: {
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    edgeExposedPx: 42,
  },
};

describe("Live2DPresentationController", () => {
  it("tries keyword, emotion, then neutral expression mappings", async () => {
    const renderer = new FakeRenderer();
    renderer.successfulExpression = "neutral-expression";
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest(manifest);

    const result = await controller.applyExpression("custom", "happy");

    expect(renderer.expressions).toEqual([
      "missing-custom",
      "missing-happy",
      "neutral-expression",
    ]);
    expect(result.source).toBe("neutral");
  });

  it("falls back from a failed model motion to the base profile", async () => {
    const renderer = new FakeRenderer();
    const controller = new Live2DPresentationController(renderer);
    controller.setManifest(manifest);

    const result = await controller.playMotion("nod");

    expect(renderer.modelMotions).toEqual([["Gesture", 1]]);
    expect(renderer.baseMotions).toEqual(["nod"]);
    expect(result.appliedSource).toBe("base");
  });
});
