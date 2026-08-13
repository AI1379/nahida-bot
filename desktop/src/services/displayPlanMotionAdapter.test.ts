import { describe, expect, it } from "vitest";

import type { DisplayPlan } from "@/domain/displayPlan";
import {
  adaptDisplayPlanToMotionIntents,
  adaptDisplaySegmentToMotionIntent,
} from "./displayPlanMotionAdapter";

describe("DisplayPlan motion adapter", () => {
  it("preserves an explicit legacy motion and maps it to semantic intent", () => {
    const result = adaptDisplaySegmentToMotionIntent(
      { text: "嗯，就是这样。", emotion: "happy", motion: "nod" },
      {
        presentationId: "reply-1",
        segmentIndex: 0,
        totalSegments: 1,
        speaking: true,
      },
    );

    expect(result.displayMotion).toBe("nod");
    expect(result.intent).toMatchObject({
      id: "reply-1:segment:0",
      source: "rule",
      intent: "agree",
      emotion: "happy",
      communicativeAct: "confirm",
      durationMs: 1180,
      priority: "speech",
    });
  });

  it("keeps the current speaking fallback while retaining emotional semantics", () => {
    const result = adaptDisplaySegmentToMotionIntent(
      { text: "我正在想这个问题。", emotion: "thinking" },
      {
        presentationId: "reply-2",
        segmentIndex: 1,
        totalSegments: 2,
        speaking: true,
        durationMs: 2300,
      },
    );

    expect(result.displayMotion).toBe("speaking");
    expect(result.totalSegments).toBe(2);
    expect(result.intent).toMatchObject({
      intent: "thinking",
      emotion: "thinking",
      gaze: "down-left",
      durationMs: 2300,
    });
    expect(result.intent.tags).toContain("display-motion:speaking");
  });

  it("keeps silent segments idle without losing critical error priority", () => {
    const result = adaptDisplaySegmentToMotionIntent(
      { text: "连接失败。", emotion: "error" },
      {
        presentationId: "error-1",
        segmentIndex: 0,
        totalSegments: 1,
        speaking: false,
      },
    );

    expect(result.displayMotion).toBe("idle");
    expect(result.intent).toMatchObject({
      intent: "error",
      priority: "critical",
      emotion: "error",
    });
  });

  it("adapts a complete plan with stable segment ids and caller timing", () => {
    const plan: DisplayPlan = {
      version: "1.0",
      text: "你好。我来说明。",
      segments: [
        { text: "你好。", motion: "wave" },
        { text: "我来说明。", voice: { style: "calm" } },
      ],
    };

    const results = adaptDisplayPlanToMotionIntents(plan, {
      presentationId: "reply-3",
      durationMs: (_segment, index) => 900 + index * 100,
      speaking: (_segment, index) => index === 1,
      source: "manual",
    });

    expect(results.map((result) => result.displayMotion)).toEqual([
      "wave",
      "speaking",
    ]);
    expect(results.map((result) => result.intent.id)).toEqual([
      "reply-3:segment:0",
      "reply-3:segment:1",
    ]);
    expect(results.map((result) => result.intent.durationMs)).toEqual([
      900,
      1000,
    ]);
    expect(results.every((result) => result.intent.source === "manual")).toBe(
      true,
    );
  });
});
