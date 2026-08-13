import type {
  MotionPlanner,
  MotionPlannerInput,
} from "@/domain/motionRuntime";
import type {
  MotionEmotion,
  MotionIntent,
  MotionIntentName,
} from "@/domain/motionIntent";

interface IntentRule {
  intent: MotionIntentName;
  pattern: RegExp;
  intensity: number;
}

const intentRules: IntentRule[] = [
  { intent: "apology", pattern: /抱歉|对不起|不好意思|sorry|apolog/iu, intensity: 0.32 },
  { intent: "greet", pattern: /你好|早上好|晚上好|嗨|hello|\bhi\b|welcome/iu, intensity: 0.55 },
  { intent: "deny", pattern: /不是|不对|不能|拒绝|否定|\bno\b|cannot|wrong/iu, intensity: 0.45 },
  { intent: "agree", pattern: /好的|没错|可以|当然|同意|\byes\b|exactly|correct/iu, intensity: 0.42 },
  { intent: "surprised", pattern: /居然|竟然|没想到|哇|天啊|surpris|wow/iu, intensity: 0.62 },
  { intent: "celebrate", pattern: /完成了|成功了|太好了|恭喜|庆祝|success|congrat/iu, intensity: 0.65 },
  { intent: "concerned", pattern: /小心|注意|担心|风险|严重|careful|warning|risk/iu, intensity: 0.4 },
  { intent: "thinking", pattern: /想一想|思考|分析|查一下|搜索|让我看看|think|search|analy/iu, intensity: 0.32 },
];

function motionEmotion(value: string | undefined): MotionEmotion {
  switch (value) {
    case "happy":
    case "thinking":
    case "worried":
    case "surprised":
    case "error":
    case "offline":
      return value;
    default:
      return "neutral";
  }
}

function intentFromEmotion(emotion: MotionEmotion): IntentRule | null {
  if (emotion === "thinking") return { intent: "thinking", pattern: /(?:)/u, intensity: 0.32 };
  if (emotion === "worried") return { intent: "concerned", pattern: /(?:)/u, intensity: 0.36 };
  if (emotion === "surprised") return { intent: "surprised", pattern: /(?:)/u, intensity: 0.62 };
  if (emotion === "error" || emotion === "offline") {
    return { intent: "error", pattern: /(?:)/u, intensity: 0.42 };
  }
  return null;
}

function priorityForIntent(intent: MotionIntentName): MotionIntent["priority"] {
  if (intent === "error") return "critical";
  if (intent === "idle") return "background";
  if (intent === "emerge" || intent === "retreat") return "state-transition";
  return "speech";
}

export class RuleMotionPlanner implements MotionPlanner {
  readonly id = "rule-motion-planner";
  readonly version = "1.0.0";

  async plan(input: MotionPlannerInput): Promise<MotionIntent> {
    const emotion = motionEmotion(input.displayEmotion);
    const rule =
      intentFromEmotion(emotion) ??
      intentRules.find((candidate) => candidate.pattern.test(input.assistantText));
    const intent = rule?.intent ?? (input.assistantText.trim() ? "explain" : "idle");
    const durationMs = Math.max(
      400,
      input.speechDurationEstimateMs ?? Math.min(8000, input.assistantText.length * 85),
    );
    return {
      id: `rule:${input.segmentIndex}`,
      source: "rule",
      intent,
      emotion,
      durationMs,
      intensity: rule?.intensity ?? (intent === "idle" ? 0.12 : 0.35),
      gaze: intent === "thinking" ? "down-left" : "user",
      loopable: intent === "idle" || intent === "thinking",
      interruptible: intent !== "emerge" && intent !== "retreat",
      priority: priorityForIntent(intent),
      tags: ["rule-planner"],
    };
  }
}
