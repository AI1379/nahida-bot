import type { DisplayEmotion, DisplayMotion } from "@/domain/displayPlan";
import { isDisplayEmotion } from "@/domain/displayPlan";
import { createDefaultModelPerformanceProfile } from "@/domain/modelPerformanceProfile";
import type {
  Live2DModelManifest,
  Live2DMotionTarget,
} from "@/domain/live2d";
import { createMotionDriverInput, type MotionDriver } from "@/domain/motionDriver";
import type { MotionIntent } from "@/domain/motionIntent";
import type { MotionPlan } from "@/domain/motionPlan";
import type {
  MotionExecutionRecord,
  MotionPlaybackSummary,
  MotionPlaybackSurface,
} from "@/domain/motionTelemetry";
import type {
  MotionLayerSource,
  MotionCache,
  MotionMixer,
  MotionPlanner,
  MotionSynthesizer,
  MotionTelemetry,
  MotionValidationResult,
  MotionValidator,
} from "@/domain/motionRuntime";
import {
  displayMotionPrimitiveMap,
  generateMotionPrimitive,
  isMotionPrimitiveName,
  motionPrimitiveDefaultDurationMs,
  motionPrimitiveIsLoopable,
  type MotionPrimitiveName,
} from "@/domain/motionPrimitives";
import type { PetRuntimeStatus, RenderMode } from "@/domain/runtime";
import type { Live2DRenderer } from "@/renderers/live2dRenderer";
import { adaptDisplaySegmentToMotionIntent } from "@/services/displayPlanMotionAdapter";
import { LocalMotionTelemetry } from "@/services/motionDatasetStorage";
import { MotionLayerScheduler } from "@/services/motionLayerScheduler";
import { createMotionCacheKey, PersistentMotionCache } from "@/services/motionCache";
import { PriorityMotionMixer } from "@/services/priorityMotionMixer";
import {
  createPrimitiveMotionPlan,
  PrimitiveMotionSynthesizer,
} from "@/services/primitiveMotionSynthesizer";
import { RuleMotionDriver } from "@/services/ruleMotionDriver";
import { RuleMotionPlanner } from "@/services/ruleMotionPlanner";
import { RuleMotionValidator } from "@/services/ruleMotionValidator";

type ExpressionCandidateSource = "keyword" | "emotion" | "neutral";
type MotionFallbackSource = "model" | "procedural" | "none";

interface ExpressionCandidate {
  source: ExpressionCandidateSource;
  name: string;
}

export interface Live2DExpressionResolution {
  requestedKey: string;
  fallbackEmotion: DisplayEmotion;
  candidates: ExpressionCandidate[];
  appliedExpression: string | null;
  source: ExpressionCandidateSource | "unchanged";
}

export interface Live2DMotionAttempt {
  source: MotionFallbackSource;
  label: string;
  applied: boolean;
}

export interface Live2DMotionResolution {
  requestedMotion: DisplayMotion;
  mappedTarget: Live2DMotionTarget | null;
  attempts: Live2DMotionAttempt[];
  appliedSource: MotionFallbackSource;
}

export interface Live2DPresentationState {
  expressionKey: string;
  emotion: DisplayEmotion;
  motion: DisplayMotion;
  renderMode: RenderMode;
  assistantText?: string;
  motionDurationMs?: number;
}

interface MotionExecutionTelemetryInput {
  intent: MotionIntent;
  primitive: MotionPrimitiveName;
  validation: MotionValidationResult;
  clip: Parameters<Live2DRenderer["playNormalizedMotion"]>[0];
  fallbackUsed: boolean;
  motionPlan: MotionPlan;
  assistantText: string;
}

function createMotionSessionId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export interface Live2DPresentationResolution {
  expression: Live2DExpressionResolution;
  motion: Live2DMotionResolution;
  renderMode: RenderMode;
  assistantText?: string;
}

export interface Live2DPresentationControllerOptions {
  motionDriver?: MotionDriver;
  motionValidator?: MotionValidator;
  motionMixer?: MotionMixer;
  motionTelemetry?: MotionTelemetry;
  motionCache?: MotionCache;
  motionPlanner?: MotionPlanner;
  motionSynthesizer?: MotionSynthesizer;
  motionDataCollectionEnabled?: () => boolean;
  playbackSurface?: MotionPlaybackSurface;
  onMotionExecuted?: (playback: MotionPlaybackSummary) => void;
}

export class Live2DPresentationController {
  private manifest: Live2DModelManifest | null = null;
  private renderMode: RenderMode = "idle";
  private motionIntentCounter = 0;
  private readonly motionSessionId = createMotionSessionId();
  private motionRequestGeneration = 0;
  private audioEnergy = 0;
  private activeMotionPlanId: string | null = null;
  private recentIntents: MotionIntent[] = [];
  private readonly motionCache: MotionCache;
  private readonly motionDriver: MotionDriver;
  private readonly motionMixer: MotionMixer;
  private readonly motionLayerScheduler: MotionLayerScheduler;
  private readonly motionPlanner: MotionPlanner;
  private readonly motionSynthesizer: MotionSynthesizer;
  private readonly motionTelemetry: MotionTelemetry;
  private readonly motionDataCollectionEnabled: () => boolean;
  private readonly playbackSurface: MotionPlaybackSurface;
  private readonly onMotionExecuted?: (playback: MotionPlaybackSummary) => void;
  private readonly motionValidator: MotionValidator;
  private readonly renderer: Live2DRenderer;

  constructor(
    renderer: Live2DRenderer,
    options: Live2DPresentationControllerOptions = {},
  ) {
    this.renderer = renderer;
    this.motionDriver = options.motionDriver ?? new RuleMotionDriver();
    this.motionCache = options.motionCache ?? new PersistentMotionCache();
    this.motionValidator = options.motionValidator ?? new RuleMotionValidator();
    this.motionMixer = options.motionMixer ?? new PriorityMotionMixer();
    this.motionLayerScheduler = new MotionLayerScheduler(
      this.motionMixer,
      this.renderer,
    );
    this.motionPlanner = options.motionPlanner ?? new RuleMotionPlanner();
    this.motionSynthesizer =
      options.motionSynthesizer ?? new PrimitiveMotionSynthesizer();
    this.motionTelemetry = options.motionTelemetry ?? new LocalMotionTelemetry();
    this.motionDataCollectionEnabled =
      options.motionDataCollectionEnabled ?? (() => true);
    this.playbackSurface = options.playbackSurface ?? "runtime";
    this.onMotionExecuted = options.onMotionExecuted;
  }

  async loadModel(manifest: Live2DModelManifest): Promise<void> {
    this.manifest = manifest;
    await this.renderer.loadModel(manifest);
    this.renderer.setFpsMode(this.renderMode);
  }

  setManifest(manifest: Live2DModelManifest): void {
    this.manifest = manifest;
    this.renderer.updateModelConfig(manifest);
  }

  async applyPresentation(
    state: Live2DPresentationState,
  ): Promise<Live2DPresentationResolution> {
    this.setRenderMode(state.renderMode);
    const expression = await this.applyExpression(
      state.expressionKey,
      state.emotion,
    );
    const motion = await this.playMotion(
      state.motion,
      state.emotion,
      state.assistantText,
      state.motionDurationMs,
    );
    return {
      expression,
      motion,
      renderMode: this.renderMode,
    };
  }

  async applyExpression(
    expressionKey: string,
    fallbackEmotion: DisplayEmotion,
  ): Promise<Live2DExpressionResolution> {
    const candidates = this.expressionCandidates(expressionKey, fallbackEmotion);
    for (const candidate of candidates) {
      if (await this.renderer.applyExpression(candidate.name)) {
        return {
          requestedKey: expressionKey,
          fallbackEmotion,
          candidates,
          appliedExpression: candidate.name,
          source: candidate.source,
        };
      }
    }

    return {
      requestedKey: expressionKey,
      fallbackEmotion,
      candidates,
      appliedExpression: null,
      source: "unchanged",
    };
  }

  async playMotion(
    motion: DisplayMotion,
    emotion: DisplayEmotion = "neutral",
    assistantText = "",
    durationMs?: number,
  ): Promise<Live2DMotionResolution> {
    const requestGeneration = ++this.motionRequestGeneration;
    this.activeMotionPlanId = null;
    const attempts: Live2DMotionAttempt[] = [];
    const mappedTarget = this.manifest?.motionMap[motion] ?? null;

    if (motion === "idle") {
      this.motionLayerScheduler.clear();
      this.renderer.clearRuntimeMotion();
    }

    if (mappedTarget?.source === "none") {
      this.motionLayerScheduler.clear();
      this.renderer.clearRuntimeMotion();
      attempts.push({
        source: "none",
        label: "mapped to none",
        applied: true,
      });
      return {
        requestedMotion: motion,
        mappedTarget,
        attempts,
        appliedSource: "none",
      };
    }

    if (mappedTarget?.source === "model") {
      this.motionLayerScheduler.clear();
      this.renderer.clearRuntimeMotion();
      const applied = await this.renderer.playModelMotion(
        mappedTarget.group,
        mappedTarget.index,
      );
      if (!this.isMotionRequestCurrent(requestGeneration)) {
        return {
          requestedMotion: motion,
          mappedTarget,
          attempts,
          appliedSource: "none",
        };
      }
      attempts.push({
        source: "model",
        label: `${mappedTarget.group} #${mappedTarget.index}`,
        applied,
      });
      if (applied) {
        return {
          requestedMotion: motion,
          mappedTarget,
          attempts,
          appliedSource: "model",
        };
      }
    }

    if (mappedTarget?.source === "procedural") {
      const applied = await this.tryProceduralMotion(
        mappedTarget.motion,
        emotion,
        this.semanticMotionText(motion, assistantText),
        attempts,
        durationMs,
        requestGeneration,
      );
      if (applied) {
        return {
          requestedMotion: motion,
          mappedTarget,
          attempts,
          appliedSource: "procedural",
        };
      }
    }

    const mappedProceduralMotion =
      mappedTarget?.source === "procedural" ? mappedTarget.motion : null;
    if (
      mappedProceduralMotion !== motion &&
      (await this.tryProceduralMotion(
        motion,
        emotion,
        this.semanticMotionText(motion, assistantText),
        attempts,
        durationMs,
        requestGeneration,
      ))
    ) {
      return {
        requestedMotion: motion,
        mappedTarget,
        attempts,
        appliedSource: "procedural",
      };
    }

    attempts.push({
      source: "none",
      label: "no compatible motion",
      applied: true,
    });
    return {
      requestedMotion: motion,
      mappedTarget,
      attempts,
      appliedSource: "none",
    };
  }

  setLipSync(value: number): void {
    this.audioEnergy = Math.max(0, Math.min(1, value));
    this.renderer.setLipSync(this.audioEnergy);
  }

  setRenderMode(mode: RenderMode): void {
    this.renderMode = mode;
    this.renderer.setFpsMode(mode);
  }

  dispose(): void {
    this.motionLayerScheduler.clear();
    this.renderer.dispose();
    this.manifest = null;
  }

  getActiveMotionPlanId(): string | null {
    return this.activeMotionPlanId;
  }

  async playPrimitive(primitive: MotionPrimitiveName): Promise<boolean> {
    const requestGeneration = ++this.motionRequestGeneration;
    this.activeMotionPlanId = null;
    const intent: MotionIntent = {
      id: this.nextMotionIntentId("debug"),
      source: "manual",
      intent: "idle",
      emotion: "neutral",
      durationMs: motionPrimitiveDefaultDurationMs(primitive),
      intensity: 0.5,
      loopable: motionPrimitiveIsLoopable(primitive),
      interruptible: true,
      priority: "background",
      tags: ["debug", `primitive:${primitive}`],
    };
    return this.driveProceduralMotion(
      intent,
      primitive,
      "",
      "debug",
      false,
      primitive,
      requestGeneration,
    );
  }

  private expressionCandidates(
    expressionKey: string,
    fallbackEmotion: DisplayEmotion,
  ): ExpressionCandidate[] {
    const candidates: ExpressionCandidate[] = [];
    const mappedKeywordExpressions = this.manifest?.emotionMap[expressionKey] ?? [];
    for (const expression of mappedKeywordExpressions) {
      candidates.push({ source: "keyword", name: expression });
    }

    if (
      !mappedKeywordExpressions.length &&
      expressionKey &&
      !isDisplayEmotion(expressionKey)
    ) {
      candidates.push({ source: "keyword", name: expressionKey });
    }

    if (fallbackEmotion !== expressionKey) {
      for (const expression of this.manifest?.emotionMap[fallbackEmotion] ?? []) {
        candidates.push({ source: "emotion", name: expression });
      }
    }

    if (fallbackEmotion !== "neutral" && expressionKey !== "neutral") {
      for (const expression of this.manifest?.emotionMap.neutral ?? []) {
        candidates.push({ source: "neutral", name: expression });
      }
    }

    return this.uniqueExpressionCandidates(candidates);
  }

  private uniqueExpressionCandidates(
    candidates: ExpressionCandidate[],
  ): ExpressionCandidate[] {
    const seen = new Set<string>();
    return candidates.filter((candidate) => {
      const name = candidate.name.trim();
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return true;
    });
  }

  private async tryProceduralMotion(
    motion: DisplayMotion,
    emotion: DisplayEmotion,
    assistantText: string,
    attempts: Live2DMotionAttempt[],
    durationMs: number | undefined,
    requestGeneration: number,
  ): Promise<boolean> {
    const primitive = displayMotionPrimitiveMap[motion];
    const adapted = adaptDisplaySegmentToMotionIntent(
      { text: assistantText, emotion, motion },
      {
        presentationId: this.nextMotionIntentId("display"),
        segmentIndex: 0,
        totalSegments: 1,
        speaking: motion === "speaking",
        durationMs:
          typeof durationMs === "number" && Number.isFinite(durationMs)
            ? Math.max(100, durationMs)
            : motionPrimitiveDefaultDurationMs(primitive),
      },
    );
    const resolved = await this.resolveMotionIntent(
      adapted.intent,
      assistantText,
      primitive,
      motion === "idle" || motion === "speaking",
      requestGeneration,
    );
    if (!this.isMotionRequestCurrent(requestGeneration)) return false;
    const applied = await this.driveProceduralMotion(
      resolved.intent,
      motion === "idle" || motion === "speaking" ? undefined : primitive,
      assistantText,
      undefined,
      resolved.cacheHit,
      primitive,
      requestGeneration,
    );
    attempts.push({
      source: "procedural",
      label: `Primitive ${primitive}`,
      applied,
    });
    return applied;
  }

  private async driveProceduralMotion(
    intent: MotionIntent,
    primitiveHint: MotionPrimitiveName | undefined,
    assistantText: string,
    source: MotionLayerSource = this.layerSourceForIntent(intent),
    cacheHit = false,
    fallbackPrimitive: MotionPrimitiveName = primitiveHint ?? "idle-breathe",
    requestGeneration = this.motionRequestGeneration,
  ): Promise<boolean> {
    const previousPose = this.renderer.getNormalizedPose();
    const profile = createDefaultModelPerformanceProfile(
      this.manifest?.id ?? "unloaded-model",
    );
    const activeProfile = this.manifest?.performanceProfile ?? profile;
    let motionPlan: MotionPlan;
    let plannedPrimitive: MotionPrimitiveName | undefined;
    try {
      motionPlan = await this.motionSynthesizer.synthesize(intent, {
        previousPose,
        audioEnergy: this.audioEnergy,
        modelProfile: activeProfile,
      });
      if (!this.isMotionRequestCurrent(requestGeneration)) return false;
      const primitiveSegment = motionPlan.segments.find(
        (segment) =>
          segment.type === "primitive" && isMotionPrimitiveName(segment.name),
      );
      if (
        primitiveSegment?.type === "primitive" &&
        isMotionPrimitiveName(primitiveSegment.name)
      ) {
        plannedPrimitive = primitiveSegment.name;
      }
    } catch {
      motionPlan = createPrimitiveMotionPlan(
        intent,
        { previousPose, audioEnergy: this.audioEnergy, modelProfile: activeProfile },
        fallbackPrimitive,
      );
    }
    if (!this.isMotionRequestCurrent(requestGeneration)) return false;
    const selectedPrimitive =
      primitiveHint ?? plannedPrimitive ?? fallbackPrimitive;
    if (plannedPrimitive !== selectedPrimitive) {
      motionPlan = createPrimitiveMotionPlan(
        intent,
        { previousPose, audioEnergy: this.audioEnergy, modelProfile: activeProfile },
        selectedPrimitive,
      );
    }
    this.recordDecision(
      intent,
      selectedPrimitive,
      assistantText,
      cacheHit,
      activeProfile.profileVersion,
    );
    let validation: MotionValidationResult | null = null;
    let invalidReason = "driver_returned_no_clip";
    try {
      const result = await this.motionDriver.drive(
        createMotionDriverInput({
          intent,
          previousPose,
          audioEnergy: this.audioEnergy,
          context: {
            runtimeStatus: this.driverRuntimeStatus(selectedPrimitive),
            modelId: this.manifest?.id,
            motionHint: selectedPrimitive,
          },
        }),
      );
      if (!this.isMotionRequestCurrent(requestGeneration)) return false;
      const drivenPrimitive = isMotionPrimitiveName(result.primitive)
        ? result.primitive
        : (primitiveHint ?? fallbackPrimitive);
      validation = result.clip
        ? this.motionValidator.validate(result.clip, {
            modelProfile: activeProfile,
            primitive: drivenPrimitive,
          })
        : null;
      if (validation?.clip) {
        const applied = this.playMixedClip(
          intent,
          validation.clip,
          source,
          requestGeneration,
        );
        if (applied) this.activeMotionPlanId = motionPlan.id;
        if (applied) {
          this.recordExecution({
            intent,
            primitive: drivenPrimitive,
            validation,
            clip: validation.clip,
            fallbackUsed: false,
            motionPlan,
            assistantText,
          });
        }
        return applied;
      }
      invalidReason = validation ? "validator_rejected" : invalidReason;
    } catch (error) {
      invalidReason = "motion_driver_failed";
      this.recordInvalid(intent, assistantText, invalidReason, {
        primitive: primitiveHint ?? fallbackPrimitive,
        error: error instanceof Error ? error.message : String(error),
      });
      // A failing learned/native driver must degrade to the local safe primitive.
    }
    const safeClip = this.safeFallbackClip(
      intent,
      previousPose,
      activeProfile.preferredIdleEnergy,
      activeProfile,
    );
    if (!safeClip) {
      this.recordInvalid(intent, assistantText, "safe_fallback_rejected", {
        primitive: primitiveHint ?? fallbackPrimitive,
      });
      return false;
    }
    if (invalidReason !== "motion_driver_failed") {
      this.recordInvalid(intent, assistantText, invalidReason, {
        primitive: primitiveHint ?? fallbackPrimitive,
        warnings: validation?.warnings ?? [],
      });
    }
    const fallbackValidation: MotionValidationResult = {
      status: "corrected",
      clip: safeClip,
      warnings: validation?.warnings ?? [],
    };
    const applied = this.playMixedClip(
      intent,
      safeClip,
      "safety",
      requestGeneration,
    );
    const safePlan = createPrimitiveMotionPlan(
      { ...intent, durationMs: safeClip.durationMs, loopable: true },
      { previousPose, audioEnergy: this.audioEnergy, modelProfile: activeProfile },
      "idle-breathe",
    );
    if (applied) this.activeMotionPlanId = safePlan.id;
    if (applied) {
      this.recordExecution({
        intent,
        primitive: "idle-breathe",
        validation: fallbackValidation,
        clip: safeClip,
        fallbackUsed: true,
        motionPlan: safePlan,
        assistantText,
      });
    }
    return applied;
  }

  private safeFallbackClip(
    intent: MotionIntent,
    previousPose: ReturnType<Live2DRenderer["getNormalizedPose"]>,
    intensity: number,
    profile: ReturnType<typeof createDefaultModelPerformanceProfile>,
  ) {
    const clip = generateMotionPrimitive("idle-breathe", {
      clipId: `${intent.id}:safe-idle`,
      intentId: intent.id,
      intensity,
      startPose: previousPose,
      loopable: true,
    });
    return this.motionValidator.validate(clip, {
      modelProfile: profile,
      primitive: "idle-breathe",
    }).clip;
  }

  private playMixedClip(
    intent: MotionIntent,
    clip: Parameters<Live2DRenderer["playNormalizedMotion"]>[0],
    source: MotionLayerSource,
    requestGeneration: number,
  ): boolean {
    if (!this.isMotionRequestCurrent(requestGeneration)) return false;
    return this.motionLayerScheduler.play(intent.id, source, clip);
  }

  private layerSourceForIntent(intent: MotionIntent): MotionLayerSource {
    if (intent.priority === "state-transition") return "state-transition";
    if (intent.priority === "critical") return "safety";
    if (intent.priority === "speech") return "speech";
    return "idle";
  }

  private recordDecision(
    intent: MotionIntent,
    primitive: MotionPrimitiveName,
    assistantText: string,
    cacheHit: boolean,
    modelProfileVersion: string,
  ): void {
    if (!this.motionDataCollectionEnabled() || !assistantText.trim()) return;
    this.ignoreTelemetryFailure(
      this.motionTelemetry.recordDecision({
        schemaVersion: 1,
        type: "motion_decision",
        timestamp: new Date().toISOString(),
        decisionId: intent.id,
        assistantText,
        runtimeStatus: this.driverRuntimeStatus(primitive),
        selectedIntent: intent,
        source: intent.source,
        modelId: this.manifest?.id ?? "unloaded-model",
        modelProfileVersion,
        plannerVersion: intent.tags?.includes("rule-planner")
          ? this.motionPlanner.version
          : "display-plan-adapter-v1",
        cacheHit,
        playbackSurface: this.playbackSurface,
      }),
    );
  }

  private recordExecution(input: MotionExecutionTelemetryInput): void {
    if (!this.motionDataCollectionEnabled() || !input.assistantText.trim()) {
      return;
    }
    const record: MotionExecutionRecord = {
      schemaVersion: 1,
      type: "motion_execution",
      timestamp: new Date().toISOString(),
      decisionId: input.intent.id,
      motionPlanId: input.motionPlan.id,
      intent: input.intent,
      modelId: this.manifest?.id ?? "unloaded-model",
      modelProfileVersion:
        this.manifest?.performanceProfile?.profileVersion ?? "default-v1",
      driverVersion: this.motionDriver.version,
      synthesizerVersion: this.motionSynthesizer.version,
      validatorVersion: this.motionValidator.version,
      mixerVersion: this.motionMixer.version,
      primitive: input.primitive,
      durationMs: input.clip.durationMs,
      frameCount: input.clip.frames.length,
      validationStatus: input.validation.status,
      validationWarnings: input.validation.warnings,
      fallbackUsed: input.fallbackUsed,
      motionPlan: input.motionPlan,
      normalizedClip: input.clip,
      playbackSurface: this.playbackSurface,
    };
    this.ignoreTelemetryFailure(this.motionTelemetry.recordExecution(record));
    this.onMotionExecuted?.({
      schemaVersion: 1,
      timestamp: record.timestamp,
      decisionId: record.decisionId,
      motionPlanId: record.motionPlanId,
      assistantText: input.assistantText,
      surface: this.playbackSurface,
      intent: record.intent,
      modelId: record.modelId,
      primitive: record.primitive,
      validationStatus: record.validationStatus,
      fallbackUsed: record.fallbackUsed,
      motionPlan: record.motionPlan,
      normalizedClip: record.normalizedClip,
    });
  }

  private recordInvalid(
    intent: MotionIntent,
    assistantText: string,
    reason: string,
    details: Record<string, unknown>,
  ): void {
    if (!this.motionDataCollectionEnabled() || !assistantText.trim()) return;
    this.ignoreTelemetryFailure(
      this.motionTelemetry.recordInvalid({
        schemaVersion: 1,
        type: "motion_invalid",
        timestamp: new Date().toISOString(),
        decisionId: intent.id,
        assistantText,
        reason,
        details,
        fallbackPlan: `${intent.id}:plan:idle-breathe`,
      }),
    );
  }

  private ignoreTelemetryFailure(task: Promise<void>): void {
    void task.catch(() => undefined);
  }

  private async resolveMotionIntent(
    intent: MotionIntent,
    assistantText: string,
    primitive: MotionPrimitiveName,
    allowSemanticPlanning: boolean,
    requestGeneration: number,
  ): Promise<{ intent: MotionIntent; cacheHit: boolean }> {
    if (!assistantText.trim()) return { intent, cacheHit: false };
    const profileVersion =
      this.manifest?.performanceProfile?.profileVersion ?? "default-v1";
    const key = createMotionCacheKey({
      assistantText,
      runtimeStatus: this.driverRuntimeStatus(primitive),
      emotion: intent.emotion,
      recentIntentNames: this.recentIntents
        .slice(-3)
        .map((recent) => recent.intent),
      modelId: this.manifest?.id ?? "unloaded-model",
      modelProfileVersion: profileVersion,
      plannerVersion: allowSemanticPlanning
        ? this.motionPlanner.version
        : "display-plan-adapter-v1",
      primitiveVersion: this.motionDriver.version,
    });
    try {
      const cached = await this.motionCache.get(key);
      if (!this.isMotionRequestCurrent(requestGeneration)) {
        return { intent, cacheHit: false };
      }
      if (cached) {
        const resolved = { ...cached.intent, id: intent.id, source: "cache" as const };
        this.rememberIntent(resolved);
        return { intent: resolved, cacheHit: true };
      }
      const planned = allowSemanticPlanning
        ? await this.motionPlanner.plan({
            assistantText,
            segmentIndex: 0,
            totalSegments: 1,
            displayEmotion: intent.emotion,
            runtimeStatus: this.driverRuntimeStatus(primitive),
            currentPoseSummary: this.renderer.getNormalizedPose(),
            recentIntents: [...this.recentIntents],
            speechDurationEstimateMs: intent.durationMs,
          })
        : intent;
      if (!this.isMotionRequestCurrent(requestGeneration)) {
        return { intent, cacheHit: false };
      }
      const resolved = { ...planned, id: intent.id };
      await this.motionCache.set({
        key,
        plannerVersion: allowSemanticPlanning
          ? this.motionPlanner.version
          : "display-plan-adapter-v1",
        modelProfileVersion: profileVersion,
        primitiveVersion: this.motionDriver.version,
        intent: resolved,
        createdAt: new Date().toISOString(),
      });
      if (!this.isMotionRequestCurrent(requestGeneration)) {
        return { intent, cacheHit: false };
      }
      this.rememberIntent(resolved);
      return { intent: resolved, cacheHit: false };
    } catch {
      // Cache/planner failures must preserve the DisplayPlan compatibility path.
    }
    this.rememberIntent(intent);
    return { intent, cacheHit: false };
  }

  private rememberIntent(intent: MotionIntent): void {
    this.recentIntents = [...this.recentIntents.slice(-7), intent];
  }

  private isMotionRequestCurrent(generation: number): boolean {
    return generation === this.motionRequestGeneration;
  }

  private semanticMotionText(motion: DisplayMotion, assistantText: string): string {
    return motion === "idle" || motion === "emerge" || motion === "retreat"
      ? ""
      : assistantText;
  }

  private driverRuntimeStatus(primitive: MotionPrimitiveName): PetRuntimeStatus {
    if (primitive === "emerge") return "emerging";
    if (primitive === "retreat") return "retreating";
    if (this.renderMode === "speaking") return "speaking";
    if (this.renderMode === "suspended") return "hidden";
    return this.renderMode === "active" ? "chat" : "emerged";
  }

  private nextMotionIntentId(prefix: string): string {
    this.motionIntentCounter += 1;
    return `${prefix}-${this.motionSessionId}-${this.motionIntentCounter}`;
  }
}
