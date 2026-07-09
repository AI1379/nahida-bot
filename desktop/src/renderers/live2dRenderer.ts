import * as PIXI from "pixi.js";
import { Live2DModel } from "pixi-live2d-display/cubism4";

import { live2dRuntimeDefaults } from "@/config/desktopRuntimeDefaults";
import type { DisplayMotion } from "@/domain/displayPlan";
import type { RenderMode } from "@/domain/runtime";
import {
  baseMotionNames,
  baseMotionProfiles,
  commonLive2DParameterIds,
  type CommonLive2DParameterRole,
} from "@/domain/live2dBaseMotion";
import type {
  Live2DModelManifest,
  Live2DMotionOption,
} from "@/domain/live2d";

import {
  createLive2DDebugSnapshot,
  readLive2DIdList,
  type CubismCoreModelDebugApi,
  type DebugOverride,
  type Live2DDebugSnapshot,
  type ModelSettingsDebugApi,
} from "./live2dDebug";
import {
  lipSyncParameterIdsForManifest,
  lipSyncValueForSpeakingPulse,
  scaleLipSyncParameterValue,
} from "./live2dLipSync";
import { clamp } from "./live2dMath";
import {
  createRuntimeParameterOverride,
  groupProceduralTargets,
  runtimeParameterValueAt,
  type RuntimeParameterOverride,
} from "./live2dProceduralMotion";

declare global {
  interface Window {
    PIXI?: typeof PIXI;
    Live2DCubismCore?: unknown;
  }
}

export interface Live2DRenderer {
  loadModel(manifest: Live2DModelManifest): Promise<void>;
  updateModelConfig(manifest: Live2DModelManifest): void;
  applyExpression(expressionName: string): Promise<boolean>;
  playModelMotion(group: string, index: number): Promise<boolean>;
  playBaseMotion(motion: DisplayMotion): boolean;
  clearRuntimeMotion(): void;
  setLipSync(value: number): void;
  setFpsMode(mode: RenderMode): void;
  dispose(): void;
}

type Live2DModelInstance = Awaited<ReturnType<typeof Live2DModel.from>>;

export type {
  Live2DDebugSnapshot,
  Live2DDrawableDebugInfo,
  Live2DExpressionDebugInfo,
  Live2DKeyParameterDebugInfo,
  Live2DMotionDebugInfo,
  Live2DParameterDebugInfo,
  Live2DPartDebugInfo,
} from "./live2dDebug";

type CommonParameterRole = CommonLive2DParameterRole;

export class WebLive2DRenderer implements Live2DRenderer {
  private app: PIXI.Application | null = null;
  private model: Live2DModelInstance | null = null;
  private manifest: Live2DModelManifest | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private renderMode: RenderMode = "idle";
  private motionBoostUntil = 0;
  private readonly host: HTMLElement;
  private readonly parameterOverrides = new Map<number, DebugOverride>();
  private readonly partOpacityOverrides = new Map<number, DebugOverride>();
  private readonly runtimeParameterOverrides = new Map<
    number,
    RuntimeParameterOverride
  >();

  constructor(host: HTMLElement) {
    this.host = host;
    window.PIXI = PIXI;
    Live2DModel.registerTicker(PIXI.Ticker);
  }

  async loadModel(manifest: Live2DModelManifest): Promise<void> {
    this.dispose();
    if (!window.Live2DCubismCore) {
      throw new Error("Live2D Cubism Core is not loaded");
    }

    this.manifest = manifest;
    const app = new PIXI.Application({
      autoDensity: true,
      backgroundAlpha: 0,
      antialias: live2dRuntimeDefaults.canvas.antialias,
      powerPreference: live2dRuntimeDefaults.canvas.powerPreference,
      resolution: Math.min(
        window.devicePixelRatio || 1,
        live2dRuntimeDefaults.canvas.maxDevicePixelRatio,
      ),
      resizeTo: this.host,
    });

    app.view.className = "live2d-canvas";
    this.host.appendChild(app.view);
    this.app = app;

    const model = await Live2DModel.from(manifest.entry, {
      autoInteract: false,
      autoUpdate: true,
    });
    model.anchor.set(
      live2dRuntimeDefaults.layout.anchorX,
      live2dRuntimeDefaults.layout.anchorY,
    );
    model.interactive = false;
    app.stage.addChild(model);
    this.model = model;
    app.ticker.add(this.applyRuntimeParameterMotion);
    app.ticker.add(this.applyDebugOverrides);

    this.resizeObserver = new ResizeObserver(() => this.fitModel());
    this.resizeObserver.observe(this.host);
    this.fitModel();
    this.setFpsMode("idle");
  }

  updateModelConfig(manifest: Live2DModelManifest): void {
    if (this.manifest?.id !== manifest.id) return;
    this.manifest = manifest;
    this.fitModel();
  }

  async applyExpression(expressionName: string): Promise<boolean> {
    if (!this.model || !expressionName) return false;
    try {
      await this.model.expression(expressionName);
      return true;
    } catch {
      // Some user models have expression files that are not declared or compatible.
      return false;
    }
  }

  async playModelMotion(group: string, index: number): Promise<boolean> {
    if (!this.model || !group) return false;
    try {
      await this.model.motion(group, index);
      return true;
    } catch {
      // User models often expose semantic motions in UI but omit .motion3.json.
      return false;
    }
  }

  playBaseMotion(motion: DisplayMotion): boolean {
    return this.playProceduralMotion(motion);
  }

  clearRuntimeMotion(): void {
    const coreModel = this.getCoreModel();
    if (coreModel?.setParameterValueByIndex) {
      for (const [index, override] of this.runtimeParameterOverrides) {
        const debugOverride = this.parameterOverrides.get(index);
        coreModel.setParameterValueByIndex(
          index,
          debugOverride?.value ?? override.original,
          1,
        );
      }
    }
    this.runtimeParameterOverrides.clear();
    this.applyLipSyncValue(0);
  }

  setLipSync(value: number): void {
    this.applyLipSyncValue(clamp(value, 0, 1));
  }

  setFpsMode(mode: RenderMode): void {
    if (!this.app) return;
    this.renderMode = mode;
    if (mode !== "speaking") {
      this.applyLipSyncValue(0);
    }
    this.applyTickerFps();
  }

  private applyTickerFps(): void {
    if (!this.app) return;
    const fps =
      this.motionBoostUntil > performance.now()
        ? live2dRuntimeDefaults.fpsByMode.active
        : live2dRuntimeDefaults.fpsByMode[this.renderMode];
    if (fps <= 0) {
      this.app.ticker.stop();
      return;
    }
    this.app.ticker.maxFPS = fps;
    if (!this.app.ticker.started) {
      this.app.ticker.start();
    }
  }

  getDebugSnapshot(): Live2DDebugSnapshot | null {
    const coreModel = this.getCoreModel();
    if (!coreModel || !this.manifest) return null;

    return createLive2DDebugSnapshot({
      coreModel,
      settings: this.getModelSettings(),
      manifest: this.manifest,
      isParameterOverridden: (index) => this.parameterOverrides.has(index),
      isRuntimeParameterOverridden: (index) =>
        this.runtimeParameterOverrides.has(index),
      isPartOpacityOverridden: (index) =>
        this.partOpacityOverrides.has(index),
    });
  }

  async setDebugExpression(name: string): Promise<void> {
    if (!this.model || !name) return;
    await this.model.expression(name);
  }

  resetDebugExpression(): void {
    const internalModel = this.model?.internalModel as
      | {
          motionManager?: {
            expressionManager?: {
              resetExpression?: () => void;
            };
          };
        }
      | undefined;
    internalModel?.motionManager?.expressionManager?.resetExpression?.();
  }

  async playDebugMotion(
    group: string,
    index: number,
    source: Live2DMotionOption["source"] = "model",
    motion?: DisplayMotion,
  ): Promise<void> {
    if (!this.model || !group) return;
    if (source === "procedural") {
      const targetMotion = motion ?? baseMotionNames[index];
      if (targetMotion) {
        this.playBaseMotion(targetMotion);
      }
      return;
    }
    await this.model.motion(group, index, 3);
  }

  setDebugPartOpacity(index: number, opacity: number): void {
    const coreModel = this.getCoreModel();
    if (!coreModel?.setPartOpacityByIndex) return;

    const value = clamp(opacity, 0, 1);
    if (!this.partOpacityOverrides.has(index)) {
      this.partOpacityOverrides.set(index, {
        value,
        original: coreModel.getPartOpacityByIndex?.(index) ?? 1,
      });
    } else {
      const current = this.partOpacityOverrides.get(index);
      if (current) {
        this.partOpacityOverrides.set(index, {
          ...current,
          value,
        });
      }
    }
    coreModel.setPartOpacityByIndex(index, value);
  }

  setDebugParameterValue(index: number, value: number): void {
    const coreModel = this.getCoreModel();
    if (!coreModel?.setParameterValueByIndex) return;

    const minimum = coreModel.getParameterMinimumValue?.(index) ?? value;
    const maximum = coreModel.getParameterMaximumValue?.(index) ?? value;
    const clamped = clamp(value, minimum, maximum);

    if (!this.parameterOverrides.has(index)) {
      this.parameterOverrides.set(index, {
        value: clamped,
        original: coreModel.getParameterValueByIndex?.(index) ?? clamped,
      });
    } else {
      const current = this.parameterOverrides.get(index);
      if (current) {
        this.parameterOverrides.set(index, {
          ...current,
          value: clamped,
        });
      }
    }
    coreModel.setParameterValueByIndex(index, clamped, 1);
  }

  resetDebugPartOpacity(index: number): void {
    const coreModel = this.getCoreModel();
    const override = this.partOpacityOverrides.get(index);
    if (!coreModel?.setPartOpacityByIndex || !override) return;

    coreModel.setPartOpacityByIndex(index, override.original);
    this.partOpacityOverrides.delete(index);
  }

  resetDebugParameterValue(index: number): void {
    const coreModel = this.getCoreModel();
    const override = this.parameterOverrides.get(index);
    if (!coreModel?.setParameterValueByIndex || !override) return;

    coreModel.setParameterValueByIndex(index, override.original, 1);
    this.parameterOverrides.delete(index);
  }

  resetDebugOverrides(): void {
    for (const index of this.partOpacityOverrides.keys()) {
      this.resetDebugPartOpacity(index);
    }
    for (const index of this.parameterOverrides.keys()) {
      this.resetDebugParameterValue(index);
    }
  }

  dispose(): void {
    this.resetDebugOverrides();
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.motionBoostUntil = 0;
    this.parameterOverrides.clear();
    this.partOpacityOverrides.clear();
    this.runtimeParameterOverrides.clear();

    // Remove ticker callbacks before destroying to prevent post-dispose ticks
    if (this.app) {
      this.app.ticker.remove(this.applyRuntimeParameterMotion);
      this.app.ticker.remove(this.applyDebugOverrides);
    }

    if (this.model) {
      this.model.destroy({ children: true, texture: true, baseTexture: true });
      this.model = null;
    }

    if (this.app) {
      this.app.destroy(true, { children: true, texture: true, baseTexture: true });
      this.app = null;
    }
  }

  private readonly applyDebugOverrides = (): void => {
    const coreModel = this.getCoreModel();
    if (!coreModel) return;

    for (const [index, override] of this.parameterOverrides) {
      coreModel.setParameterValueByIndex?.(index, override.value, 1);
    }
    for (const [index, override] of this.partOpacityOverrides) {
      coreModel.setPartOpacityByIndex?.(index, override.value);
    }
  };

  private readonly applyRuntimeParameterMotion = (): void => {
    const coreModel = this.getCoreModel();
    if (!coreModel) return;

    const now = performance.now();
    for (const [index, override] of this.runtimeParameterOverrides) {
      const elapsed = now - override.startedAt;
      if (elapsed >= override.durationMs) {
        const debugOverride = this.parameterOverrides.get(index);
        coreModel.setParameterValueByIndex?.(
          index,
          debugOverride?.value ?? override.original,
          1,
        );
        this.runtimeParameterOverrides.delete(index);
        continue;
      }
      coreModel.setParameterValueByIndex?.(
        index,
        runtimeParameterValueAt(override, elapsed),
        1,
      );
    }

    if (this.motionBoostUntil > 0 && now >= this.motionBoostUntil) {
      this.motionBoostUntil = 0;
      this.applyTickerFps();
    }

    if (this.renderMode === "speaking") {
      this.applyLipSyncValue(lipSyncValueForSpeakingPulse(now));
    }
  };

  private fitModel(): void {
    if (!this.app || !this.model) return;

    const width = this.host.clientWidth;
    const height = this.host.clientHeight;
    if (width <= 0 || height <= 0) return;

    this.app.renderer.resize(width, height);
    this.model.scale.set(1);
    const modelWidth = Math.max(this.model.width, 1);
    const modelHeight = Math.max(this.model.height, 1);
    const layout = this.manifest?.layout;
    const scale =
      Math.min(width / modelWidth, height / modelHeight) *
      live2dRuntimeDefaults.layout.fitScale *
      (layout?.scale ?? 1);

    this.model.scale.set(scale);
    this.model.position.set(
      width * live2dRuntimeDefaults.layout.positionXRatio +
        (layout?.offsetX ?? 0),
      height * live2dRuntimeDefaults.layout.positionYRatio +
        (layout?.offsetY ?? 0),
    );
  }

  private getCoreModel(): CubismCoreModelDebugApi | null {
    const internalModel = this.model?.internalModel as
      | { coreModel?: CubismCoreModelDebugApi }
      | undefined;
    return internalModel?.coreModel ?? null;
  }

  private getModelSettings(): ModelSettingsDebugApi | null {
    const internalModel = this.model?.internalModel as
      | { settings?: ModelSettingsDebugApi }
      | undefined;
    return internalModel?.settings ?? null;
  }

  private playProceduralMotion(motion: DisplayMotion): boolean {
    const coreModel = this.getCoreModel();
    const profile = baseMotionProfiles[motion];
    if (!coreModel || !profile) return false;

    this.clearRuntimeMotion();
    const now = performance.now();
    this.motionBoostUntil = Math.max(
      this.motionBoostUntil,
      now +
        profile.durationMs +
        live2dRuntimeDefaults.motion.boostTailMs,
    );
    this.applyTickerFps();

    let applied = false;
    const targetsByRole = groupProceduralTargets(profile);
    for (const [role, targets] of targetsByRole) {
      for (const index of this.parameterIndicesForRole(role)) {
        const current =
          coreModel.getParameterValueByIndex?.(index) ??
          coreModel.getParameterDefaultValue?.(index) ??
          0;
        const minimum = coreModel.getParameterMinimumValue?.(index) ?? current;
        const maximum = coreModel.getParameterMaximumValue?.(index) ?? current;
        this.runtimeParameterOverrides.set(
          index,
          createRuntimeParameterOverride({
            profile,
            current,
            minimum,
            maximum,
            targets,
            startedAt: now,
          }),
        );
        applied = true;
      }
    }

    return applied;
  }

  private applyLipSyncValue(value: number): void {
    const coreModel = this.getCoreModel();
    if (!coreModel || !this.manifest?.lipSync.enabled) return;

    const parameterIds = lipSyncParameterIdsForManifest(this.manifest);

    for (const id of parameterIds) {
      const index = this.parameterIndexById(id);
      if (index === null || this.parameterOverrides.has(index)) continue;
      const minimum = coreModel.getParameterMinimumValue?.(index) ?? 0;
      const maximum = coreModel.getParameterMaximumValue?.(index) ?? 1;
      const nextValue = scaleLipSyncParameterValue(id, value);
      coreModel.setParameterValueByIndex?.(
        index,
        clamp(nextValue, minimum, maximum),
        1,
      );
    }
  }

  private parameterIndicesForRole(role: CommonParameterRole): number[] {
    return commonLive2DParameterIds[role].flatMap((id) => {
      const index = this.parameterIndexById(id);
      return index === null ? [] : [index];
    });
  }

  private parameterIndexById(id: string): number | null {
    const coreModel = this.getCoreModel();
    if (!coreModel) return null;
    const ids = readLive2DIdList(coreModel._parameterIds);
    const index = ids.findIndex(
      (candidate) => candidate.toLowerCase() === id.toLowerCase(),
    );
    return index >= 0 ? index : null;
  }
}
