import { describe, expect, it } from "vitest";

import type { MotionPlannerInput } from "@/domain/motionRuntime";
import { motionBenchmarkScenarios } from "@/fixtures/motionBenchmarkScenarios";
import { RuleMotionPlanner } from "./ruleMotionPlanner";

function input(
  assistantText: string,
  displayEmotion = "neutral",
): MotionPlannerInput {
  return {
    assistantText,
    segmentIndex: 0,
    totalSegments: 1,
    displayEmotion,
    runtimeStatus: "speaking",
    currentPoseSummary: {},
    recentIntents: [],
    speechDurationEstimateMs: 1200,
  };
}

describe("RuleMotionPlanner", () => {
  it.each([
    ["你好，很高兴见到你。", "greet"],
    ["对不起，我刚才理解错了。", "apology"],
    ["不对，这个答案需要修正。", "deny"],
    ["太好了，任务成功了！", "celebrate"],
    ["让我先查一下这个问题。", "thinking"],
  ])("maps %s to %s", async (text, expectedIntent) => {
    const result = await new RuleMotionPlanner().plan(input(text));

    expect(result.intent).toBe(expectedIntent);
    expect(result.source).toBe("rule");
    expect(result.durationMs).toBe(1200);
  });

  it("uses strong display emotion before text fallback", async () => {
    const result = await new RuleMotionPlanner().plan(
      input("我来解释一下。", "error"),
    );

    expect(result).toMatchObject({ intent: "error", priority: "critical" });
  });

  it("passes the fixed 50-scenario semantic benchmark", async () => {
    const planner = new RuleMotionPlanner();
    const results = await Promise.all(
      motionBenchmarkScenarios.map(async (scenario) => ({
        id: scenario.id,
        actual: (
          await planner.plan(
            input(scenario.assistantText, scenario.displayEmotion),
          )
        ).intent,
        expected: scenario.expectedIntent,
      })),
    );

    expect(motionBenchmarkScenarios).toHaveLength(50);
    expect(results.filter((result) => result.actual !== result.expected)).toEqual(
      [],
    );
  });
});
