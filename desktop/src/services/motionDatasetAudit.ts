import type {
  MotionDatasetKind,
  MotionDatasetRecords,
} from "@/domain/motionTelemetry";
import { activeMotionPreferences } from "./motionDatasetStorage";

export interface MotionDatasetAuditIssue {
  kind: MotionDatasetKind;
  index: number;
  severity: "error" | "warning";
  message: string;
}

export interface MotionDatasetReadinessCriterion {
  id:
    | "valid-records"
    | "decision-volume"
    | "preference-volume"
    | "intent-coverage"
    | "execution-linkage"
    | "preference-linkage";
  label: string;
  current: number;
  target: number;
  unit: "count" | "ratio";
  passed: boolean;
}

export interface MotionDatasetAuditReport {
  counts: Record<MotionDatasetKind, number>;
  issues: MotionDatasetAuditIssue[];
  validRecordRatio: number;
  executionLinkageRatio: number;
  preferenceLinkageRatio: number;
  distinctIntentCount: number;
  distinctPrimitiveCount: number;
  fallbackRatio: number;
  correctionRatio: number;
  criteria: MotionDatasetReadinessCriterion[];
  readyForTraining: boolean;
}

export type MotionDatasetAuditInput = Partial<{
  [K in MotionDatasetKind]: readonly unknown[];
}>;

const kinds: MotionDatasetKind[] = [
  "decisions",
  "executions",
  "preferences",
  "invalid",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function hasValidBase(record: Record<string, unknown>): boolean {
  return record.schemaVersion === 1 &&
    isNonEmptyString(record.timestamp) &&
    !Number.isNaN(Date.parse(record.timestamp));
}

function validationMessage(
  kind: MotionDatasetKind,
  value: unknown,
): string | null {
  if (!isRecord(value)) return "Record is not a JSON object.";
  if (!hasValidBase(value)) {
    return "Record has an unsupported schema version or invalid timestamp.";
  }
  if (kind === "decisions") {
    if (value.type !== "motion_decision" ||
      !isNonEmptyString(value.decisionId) ||
      !isNonEmptyString(value.modelId) ||
      !isNonEmptyString(value.modelProfileVersion) ||
      !isNonEmptyString(value.plannerVersion) ||
      !isRecord(value.selectedIntent) ||
      !isNonEmptyString(value.selectedIntent.intent)) {
      return "Decision is missing its identity, planner, model, or selected intent.";
    }
  } else if (kind === "executions") {
    if (value.type !== "motion_execution" ||
      !isNonEmptyString(value.decisionId) ||
      !isNonEmptyString(value.motionPlanId) ||
      !isNonEmptyString(value.modelProfileVersion) ||
      !isNonEmptyString(value.synthesizerVersion) ||
      !isNonEmptyString(value.validatorVersion) ||
      !isRecord(value.motionPlan) ||
      !isRecord(value.normalizedClip) ||
      !isNonEmptyString(value.primitive) ||
      typeof value.durationMs !== "number" || value.durationMs <= 0 ||
      typeof value.frameCount !== "number" || value.frameCount < 2) {
      return "Execution is missing its versioned plan, clip, primitive, or frame metadata.";
    }
  } else if (kind === "preferences") {
    if (value.type === "motion_preference_retraction") {
      if (!isNonEmptyString(value.retractionId) ||
        !isNonEmptyString(value.retractsPreferenceId) ||
        !isNonEmptyString(value.motionPlanId)) {
        return "Preference retraction is missing its identity or target.";
      }
    } else if (value.type !== "motion_preference" ||
        !isNonEmptyString(value.preferenceId) ||
        !isNonEmptyString(value.candidateA) ||
        !Array.isArray(value.labels) || value.labels.length === 0) {
      return "Preference is missing its identity, candidate, or feedback labels.";
    }
  } else if (value.type !== "motion_invalid" ||
    !isNonEmptyString(value.decisionId) ||
    !isNonEmptyString(value.reason)) {
    return "Invalid sample is missing its decision link or reason.";
  }
  return null;
}

function ratio(numerator: number, denominator: number): number {
  return denominator > 0 ? numerator / denominator : 0;
}

function isTrainingSurface(surface: string | undefined): boolean {
  return surface !== "workbench" && surface !== "debug";
}

export function auditMotionDataset(
  input: MotionDatasetAuditInput,
): MotionDatasetAuditReport {
  const rawCounts = Object.fromEntries(
    kinds.map((kind) => [kind, input[kind]?.length ?? 0]),
  ) as Record<MotionDatasetKind, number>;
  const issues: MotionDatasetAuditIssue[] = [];
  const valid: {
    [K in MotionDatasetKind]: MotionDatasetRecords[K][];
  } = {
    decisions: [],
    executions: [],
    preferences: [],
    invalid: [],
  };

  for (const kind of kinds) {
    (input[kind] ?? []).forEach((value, index) => {
      const message = validationMessage(kind, value);
      if (message) {
        issues.push({ kind, index, severity: "error", message });
        return;
      }
      valid[kind].push(value as never);
    });
  }

  const trainingDecisions = valid.decisions.filter((record) =>
    isTrainingSurface(record.playbackSurface)
  );
  const trainingExecutions = valid.executions.filter((record) =>
    isTrainingSurface(record.playbackSurface)
  );
  const decisionIds = new Set(
    trainingDecisions.map((record) => record.decisionId),
  );
  const linkedExecutions = trainingExecutions.filter((record, index) => {
    if (decisionIds.has(record.decisionId)) return true;
    issues.push({
      kind: "executions",
      index,
      severity: "warning",
      message: `Execution references missing decision ${record.decisionId}.`,
    });
    return false;
  });
  const executedDecisionIds = new Set(
    linkedExecutions.map((record) => record.decisionId),
  );
  const motionPlanIds = new Set(
    trainingExecutions.map((record) => record.motionPlanId),
  );
  const activePreferences = activeMotionPreferences(valid.preferences).filter(
    (record) =>
      isTrainingSurface(record.ratedSurface ?? record.playbackSurface),
  );
  const linkedPreferences = activePreferences.filter((record, index) => {
    const linked = motionPlanIds.has(record.candidateA) &&
      (!record.candidateB || motionPlanIds.has(record.candidateB));
    if (linked) return true;
    issues.push({
      kind: "preferences",
      index,
      severity: "warning",
      message: `Preference references a missing motion plan candidate.`,
    });
    return false;
  });
  const duplicateDecisionIds = new Set<string>();
  const seenDecisionIds = new Set<string>();
  trainingDecisions.forEach((record, index) => {
    if (!seenDecisionIds.has(record.decisionId)) {
      seenDecisionIds.add(record.decisionId);
      return;
    }
    duplicateDecisionIds.add(record.decisionId);
    issues.push({
      kind: "decisions",
      index,
      severity: "warning",
      message: `Duplicate decision id ${record.decisionId}.`,
    });
  });

  const distinctIntents = new Set(
    trainingDecisions.map((record) => record.selectedIntent.intent),
  );
  const distinctPrimitives = new Set(
    trainingExecutions.map((record) => record.primitive),
  );
  const totalCount = Object.values(rawCounts).reduce(
    (sum, count) => sum + count,
    0,
  );
  const errorCount = issues.filter((issue) => issue.severity === "error").length;
  const validRecordRatio = totalCount > 0
    ? Math.max(0, (totalCount - errorCount) / totalCount)
    : 0;
  const executionLinkageRatio = ratio(
    linkedExecutions.length,
    trainingExecutions.length,
  );
  const preferenceLinkageRatio = ratio(
    linkedPreferences.length,
    activePreferences.length,
  );
  const counts = {
    ...rawCounts,
    decisions: trainingDecisions.length,
    executions: trainingExecutions.length,
    preferences: activePreferences.length,
  };

  const criteria: MotionDatasetReadinessCriterion[] = [
    {
      id: "valid-records",
      label: "Valid records",
      current: validRecordRatio,
      target: 0.99,
      unit: "ratio",
      passed: validRecordRatio >= 0.99,
    },
    {
      id: "decision-volume",
      label: "Linked motion decisions",
      current: executedDecisionIds.size,
      target: 500,
      unit: "count",
      passed: executedDecisionIds.size >= 500,
    },
    {
      id: "preference-volume",
      label: "Human preferences",
      current: activePreferences.length,
      target: 100,
      unit: "count",
      passed: activePreferences.length >= 100,
    },
    {
      id: "intent-coverage",
      label: "Distinct intents",
      current: distinctIntents.size,
      target: 8,
      unit: "count",
      passed: distinctIntents.size >= 8,
    },
    {
      id: "execution-linkage",
      label: "Linked executions",
      current: executionLinkageRatio,
      target: 0.95,
      unit: "ratio",
      passed: executionLinkageRatio >= 0.95,
    },
    {
      id: "preference-linkage",
      label: "Linked preferences",
      current: preferenceLinkageRatio,
      target: 0.95,
      unit: "ratio",
      passed: preferenceLinkageRatio >= 0.95,
    },
  ];

  return {
    counts,
    issues,
    validRecordRatio,
    executionLinkageRatio,
    preferenceLinkageRatio,
    distinctIntentCount: distinctIntents.size,
    distinctPrimitiveCount: distinctPrimitives.size,
    fallbackRatio: ratio(
      trainingExecutions.filter((record) => record.fallbackUsed).length,
      trainingExecutions.length,
    ),
    correctionRatio: ratio(
      trainingExecutions.filter(
        (record) => record.validationStatus === "corrected",
      ).length,
      trainingExecutions.length,
    ),
    criteria,
    readyForTraining: criteria.every((criterion) => criterion.passed) &&
      duplicateDecisionIds.size === 0,
  };
}
