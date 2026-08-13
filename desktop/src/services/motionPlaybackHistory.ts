import type {
  MotionDecisionRecord,
  MotionExecutionRecord,
  MotionPlaybackSummary,
} from "@/domain/motionTelemetry";
import { readMotionDataset } from "./motionDatasetStorage";

function isRealUsageSurface(
  surface: MotionPlaybackSummary["surface"],
): boolean {
  return surface !== "workbench" && surface !== "debug";
}

export function motionPlaybackSummaryFromRecords(
  decision: MotionDecisionRecord,
  execution: MotionExecutionRecord,
): MotionPlaybackSummary {
  return {
    schemaVersion: 1,
    timestamp: execution.timestamp,
    decisionId: execution.decisionId,
    motionPlanId: execution.motionPlanId,
    assistantText: decision.assistantText,
    surface:
      execution.playbackSurface ?? decision.playbackSurface ?? "legacy",
    intent: execution.intent,
    modelId: execution.modelId,
    primitive: execution.primitive,
    validationStatus: execution.validationStatus,
    fallbackUsed: execution.fallbackUsed,
    motionPlan: execution.motionPlan,
    normalizedClip: execution.normalizedClip,
  };
}

export function mergeRecentMotionPlaybacks(
  current: readonly MotionPlaybackSummary[],
  incoming: readonly MotionPlaybackSummary[],
  limit = 20,
): MotionPlaybackSummary[] {
  const byPlan = new Map<string, MotionPlaybackSummary>();
  for (const playback of [...current, ...incoming]) {
    const previous = byPlan.get(playback.motionPlanId);
    if (!previous || playback.timestamp > previous.timestamp) {
      byPlan.set(playback.motionPlanId, playback);
    }
  }
  return [...byPlan.values()]
    .filter(
      (playback) =>
        playback.assistantText.trim() && isRealUsageSurface(playback.surface),
    )
    .sort((left, right) => right.timestamp.localeCompare(left.timestamp))
    .slice(0, Math.max(1, limit));
}

export async function readRecentMotionPlaybacks(
  limit = 20,
): Promise<MotionPlaybackSummary[]> {
  const [decisions, executions] = await Promise.all([
    readMotionDataset("decisions"),
    readMotionDataset("executions"),
  ]);
  const decisionsById = new Map(
    decisions.map((decision) => [decision.decisionId, decision]),
  );
  const summaries = executions.flatMap((execution) => {
    const decision = decisionsById.get(execution.decisionId);
    return decision
      ? [motionPlaybackSummaryFromRecords(decision, execution)]
      : [];
  });
  return mergeRecentMotionPlaybacks([], summaries, limit);
}
