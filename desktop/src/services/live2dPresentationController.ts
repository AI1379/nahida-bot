import type { DisplayEmotion, DisplayMotion } from "@/domain/displayPlan";
import { isDisplayEmotion } from "@/domain/displayPlan";
import { hasBaseMotionProfile } from "@/domain/live2dBaseMotion";
import type {
  Live2DModelManifest,
  Live2DMotionTarget,
} from "@/domain/live2d";
import type { RenderMode } from "@/domain/runtime";
import type { Live2DRenderer } from "@/renderers/live2dRenderer";

type ExpressionCandidateSource = "keyword" | "emotion" | "neutral";
type MotionFallbackSource = "model" | "base" | "none";

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
}

export interface Live2DPresentationResolution {
  expression: Live2DExpressionResolution;
  motion: Live2DMotionResolution;
  renderMode: RenderMode;
}

export class Live2DPresentationController {
  private manifest: Live2DModelManifest | null = null;
  private renderMode: RenderMode = "idle";
  private readonly renderer: Live2DRenderer;

  constructor(renderer: Live2DRenderer) {
    this.renderer = renderer;
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
    const motion = await this.playMotion(state.motion);
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

  async playMotion(motion: DisplayMotion): Promise<Live2DMotionResolution> {
    const attempts: Live2DMotionAttempt[] = [];
    const mappedTarget = this.manifest?.motionMap[motion] ?? null;

    if (motion === "idle") {
      this.renderer.clearRuntimeMotion();
    }

    if (mappedTarget?.source === "none") {
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
      const applied = await this.renderer.playModelMotion(
        mappedTarget.group,
        mappedTarget.index,
      );
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
      const applied = this.tryBaseMotion(mappedTarget.motion, attempts);
      if (applied) {
        return {
          requestedMotion: motion,
          mappedTarget,
          attempts,
          appliedSource: "base",
        };
      }
    }

    const mappedBaseMotion =
      mappedTarget?.source === "procedural" ? mappedTarget.motion : null;
    if (
      motion !== "idle" &&
      mappedBaseMotion !== motion &&
      this.tryBaseMotion(motion, attempts)
    ) {
      return {
        requestedMotion: motion,
        mappedTarget,
        attempts,
        appliedSource: "base",
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
    this.renderer.setLipSync(value);
  }

  setRenderMode(mode: RenderMode): void {
    this.renderMode = mode;
    this.renderer.setFpsMode(mode);
  }

  dispose(): void {
    this.renderer.dispose();
    this.manifest = null;
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

  private tryBaseMotion(
    motion: DisplayMotion,
    attempts: Live2DMotionAttempt[],
  ): boolean {
    if (!hasBaseMotionProfile(motion)) return false;
    const applied = this.renderer.playBaseMotion(motion);
    attempts.push({
      source: "base",
      label: `Base ${motion}`,
      applied,
    });
    return applied;
  }
}
