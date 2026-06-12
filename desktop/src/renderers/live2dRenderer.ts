import * as PIXI from "pixi.js";
import { Live2DModel } from "pixi-live2d-display/cubism4";

import { live2dRuntimeDefaults } from "@/config/desktopRuntimeDefaults";
import type { DisplayMotion } from "@/domain/displayPlan";
import type { RenderMode } from "@/domain/runtime";
import {
  baseMotionNames,
  baseMotionProfiles,
  commonLive2DParameterIds,
  type BaseMotionProfile,
  type CommonLive2DParameterRole,
} from "@/domain/live2dBaseMotion";
import type {
  Live2DExpressionOption,
  Live2DModelManifest,
  Live2DMotionOption,
} from "@/domain/live2d";

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

export interface Live2DParameterDebugInfo {
  index: number;
  id: string;
  value: number;
  minimum: number;
  maximum: number;
  defaultValue: number;
  overridden: boolean;
  runtimeOverridden: boolean;
}

export interface Live2DKeyParameterDebugInfo
  extends Live2DParameterDebugInfo {
  roles: CommonLive2DParameterRole[];
  lipSync: boolean;
}

export interface Live2DPartDebugInfo {
  index: number;
  id: string;
  opacity: number;
  overridden: boolean;
}

export interface Live2DDrawableDebugInfo {
  index: number;
  id: string;
  opacity: number;
  visible: boolean;
  renderOrder: number;
  textureIndex: number;
  vertexCount: number;
  bounds: {
    x: number;
    y: number;
    width: number;
    height: number;
    area: number;
  } | null;
}

export interface Live2DExpressionDebugInfo extends Live2DExpressionOption {}

export interface Live2DMotionDebugInfo extends Live2DMotionOption {}

export interface Live2DDebugSnapshot {
  modelName: string;
  expressions: Live2DExpressionDebugInfo[];
  nativeMotions: Live2DMotionDebugInfo[];
  baseMotions: Live2DMotionDebugInfo[];
  motions: Live2DMotionDebugInfo[];
  keyParameters: Live2DKeyParameterDebugInfo[];
  parameters: Live2DParameterDebugInfo[];
  parts: Live2DPartDebugInfo[];
  drawables: Live2DDrawableDebugInfo[];
}

interface CubismCoreModelDebugApi {
  getModel?: () => unknown;
  getParameterCount?: () => number;
  getParameterMinimumValue?: (index: number) => number;
  getParameterMaximumValue?: (index: number) => number;
  getParameterDefaultValue?: (index: number) => number;
  getParameterValueByIndex?: (index: number) => number;
  setParameterValueByIndex?: (
    index: number,
    value: number,
    weight?: number,
  ) => void;
  getPartCount?: () => number;
  getPartOpacityByIndex?: (index: number) => number;
  setPartOpacityByIndex?: (index: number, opacity: number) => void;
  getDrawableCount?: () => number;
  getDrawableId?: (index: number) => string;
  getDrawableOpacity?: (index: number) => number;
  getDrawableDynamicFlagIsVisible?: (index: number) => boolean;
  getDrawableRenderOrders?: () => ArrayLike<number>;
  getDrawableTextureIndices?: (index: number) => number;
  getDrawableVertexCount?: (index: number) => number;
  getDrawableVertexPositions?: (index: number) => ArrayLike<number>;
  getDrawableVertices?: (index: number) => ArrayLike<number>;
  _parameterIds?: unknown;
  _partIds?: unknown;
}

interface ModelSettingsDebugApi {
  expressions?: unknown[];
  motions?: Record<string, unknown[]>;
  json?: {
    FileReferences?: {
      Expressions?: unknown[];
      Motions?: Record<string, unknown[]>;
    };
  };
}

interface DebugOverride {
  value: number;
  original: number;
}

type CommonParameterRole = CommonLive2DParameterRole;

interface RuntimeParameterKeyframe {
  atMs: number;
  value: number;
}

interface RuntimeParameterOverride {
  original: number;
  keyframes: RuntimeParameterKeyframe[];
  startedAt: number;
  durationMs: number;
}

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
    this.applyLipSyncValue(this.clamp(value, 0, 1));
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

    const settings = this.getModelSettings();
    const parameterIds = this.readIdList(coreModel._parameterIds);
    const partIds = this.readIdList(coreModel._partIds);
    const renderOrders = coreModel.getDrawableRenderOrders?.();

    const parameters = Array.from(
      { length: coreModel.getParameterCount?.() ?? 0 },
      (_, index) => ({
        index,
        id: this.idAt(parameterIds, index, `Parameter #${index}`),
        value: coreModel.getParameterValueByIndex?.(index) ?? 0,
        minimum: coreModel.getParameterMinimumValue?.(index) ?? 0,
        maximum: coreModel.getParameterMaximumValue?.(index) ?? 1,
        defaultValue: coreModel.getParameterDefaultValue?.(index) ?? 0,
        overridden: this.parameterOverrides.has(index),
        runtimeOverridden: this.runtimeParameterOverrides.has(index),
      }),
    );

    const parts = Array.from(
      { length: coreModel.getPartCount?.() ?? 0 },
      (_, index) => ({
        index,
        id: this.idAt(partIds, index, `Part #${index}`),
        opacity: coreModel.getPartOpacityByIndex?.(index) ?? 1,
        overridden: this.partOpacityOverrides.has(index),
      }),
    );

    const drawables = Array.from(
      { length: coreModel.getDrawableCount?.() ?? 0 },
      (_, index) => ({
        index,
        id: coreModel.getDrawableId?.(index) ?? `Drawable #${index}`,
        opacity: coreModel.getDrawableOpacity?.(index) ?? 1,
        visible: coreModel.getDrawableDynamicFlagIsVisible?.(index) ?? true,
        renderOrder: renderOrders?.[index] ?? index,
        textureIndex: coreModel.getDrawableTextureIndices?.(index) ?? 0,
        vertexCount: coreModel.getDrawableVertexCount?.(index) ?? 0,
        bounds: this.getDrawableBounds(coreModel, index),
      }),
    ).sort(
      (left, right) => (right.bounds?.area ?? 0) - (left.bounds?.area ?? 0),
    );

    const nativeMotions = this.getNativeMotionDebugInfo(settings);
    const baseMotions = this.getBaseMotionDebugInfo();

    return {
      modelName: this.manifest.name,
      expressions: this.getExpressionDebugInfo(settings),
      nativeMotions,
      baseMotions,
      motions: [...nativeMotions, ...baseMotions],
      keyParameters: this.getKeyParameterDebugInfo(parameters),
      parameters,
      parts,
      drawables,
    };
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

    const value = this.clamp(opacity, 0, 1);
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
    const clamped = this.clamp(value, minimum, maximum);

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
        this.valueAtRuntimeKeyframe(override, elapsed),
        1,
      );
    }

    if (this.motionBoostUntil > 0 && now >= this.motionBoostUntil) {
      this.motionBoostUntil = 0;
      this.applyTickerFps();
    }

    if (this.renderMode === "speaking") {
      const pulse =
        (Math.sin(
          now / live2dRuntimeDefaults.lipSync.pulsePeriodMs,
        ) +
          1) /
        2;
      this.applyLipSyncValue(
        live2dRuntimeDefaults.lipSync.minimumOpen +
          pulse * live2dRuntimeDefaults.lipSync.openRange,
      );
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
    const targetsByRole = this.groupProceduralTargets(profile);
    for (const [role, targets] of targetsByRole) {
      for (const index of this.parameterIndicesForRole(role)) {
        const current =
          coreModel.getParameterValueByIndex?.(index) ??
          coreModel.getParameterDefaultValue?.(index) ??
          0;
        const minimum = coreModel.getParameterMinimumValue?.(index) ?? current;
        const maximum = coreModel.getParameterMaximumValue?.(index) ?? current;
        const keyframes = [
          { atMs: 0, value: current },
          ...targets.map((target) => ({
            atMs: target.atMs,
            value: this.clamp(target.value, minimum, maximum),
          })),
          { atMs: profile.durationMs, value: current },
        ].sort((left, right) => left.atMs - right.atMs);

        this.runtimeParameterOverrides.set(index, {
          original: current,
          keyframes,
          startedAt: now,
          durationMs: profile.durationMs,
        });
        applied = true;
      }
    }

    return applied;
  }

  private getBaseMotionDebugInfo(): Live2DMotionDebugInfo[] {
    return baseMotionNames.map((motion, index) => ({
      source: "procedural",
      group: "Base",
      index,
      name: motion,
      file: "common Live2D parameters",
      motion,
    }));
  }

  private groupProceduralTargets(
    profile: BaseMotionProfile,
  ): Map<CommonParameterRole, RuntimeParameterKeyframe[]> {
    const grouped = new Map<CommonParameterRole, RuntimeParameterKeyframe[]>();
    for (const keyframe of profile.keyframes) {
      for (const target of keyframe.targets) {
        const values = grouped.get(target.role) ?? [];
        values.push({
          atMs: keyframe.atMs,
          value: target.value,
        });
        grouped.set(target.role, values);
      }
    }
    return grouped;
  }

  private valueAtRuntimeKeyframe(
    override: RuntimeParameterOverride,
    elapsedMs: number,
  ): number {
    const keyframes = override.keyframes;
    if (keyframes.length === 0) return override.original;

    let previous = keyframes[0];
    let next = keyframes[keyframes.length - 1];
    for (let index = 1; index < keyframes.length; index += 1) {
      next = keyframes[index];
      if (elapsedMs <= next.atMs) break;
      previous = next;
    }

    const duration = Math.max(next.atMs - previous.atMs, 1);
    const progress = this.smoothstep((elapsedMs - previous.atMs) / duration);
    return this.lerp(previous.value, next.value, progress);
  }

  private applyLipSyncValue(value: number): void {
    const coreModel = this.getCoreModel();
    if (!coreModel || !this.manifest?.lipSync.enabled) return;

    const parameterIds = this.manifest.lipSync.parameterIds.length
      ? this.manifest.lipSync.parameterIds
      : commonLive2DParameterIds.mouthOpen;

    for (const id of parameterIds) {
      const index = this.parameterIndexById(id);
      if (index === null || this.parameterOverrides.has(index)) continue;
      const minimum = coreModel.getParameterMinimumValue?.(index) ?? 0;
      const maximum = coreModel.getParameterMaximumValue?.(index) ?? 1;
      const nextValue = /form/i.test(id)
        ? value * live2dRuntimeDefaults.lipSync.mouthFormScale
        : value;
      coreModel.setParameterValueByIndex?.(
        index,
        this.clamp(nextValue, minimum, maximum),
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
    const ids = this.readIdList(coreModel._parameterIds);
    const index = ids.findIndex(
      (candidate) => candidate.toLowerCase() === id.toLowerCase(),
    );
    return index >= 0 ? index : null;
  }

  private getExpressionDebugInfo(
    settings: ModelSettingsDebugApi | null,
  ): Live2DExpressionDebugInfo[] {
    const definitions =
      settings?.expressions ?? settings?.json?.FileReferences?.Expressions ?? [];

    return definitions.map((definition, index) => {
      const file = this.stringField(definition, ["File", "file"]) ?? "";
      const name =
        this.stringField(definition, ["Name", "name", "Id", "id"]) ??
        file ??
        `Expression #${index}`;

      return {
        index,
        name,
        file,
      };
    });
  }

  private getNativeMotionDebugInfo(
    settings: ModelSettingsDebugApi | null,
  ): Live2DMotionDebugInfo[] {
    const groups =
      settings?.motions ?? settings?.json?.FileReferences?.Motions ?? {};

    return Object.entries(groups).flatMap(([group, definitions]) =>
      (definitions ?? []).map((definition, index) => ({
        source: "model" as const,
        group,
        index,
        name: `${group} #${index}`,
        file: this.stringField(definition, ["File", "file"]) ?? "",
      })),
    );
  }

  private getKeyParameterDebugInfo(
    parameters: Live2DParameterDebugInfo[],
  ): Live2DKeyParameterDebugInfo[] {
    const lipSyncParameterIds = new Set(
      (this.manifest?.lipSync.parameterIds.length
        ? this.manifest.lipSync.parameterIds
        : commonLive2DParameterIds.mouthOpen
      ).map((id) => id.toLowerCase()),
    );

    return parameters.flatMap((parameter) => {
      const roles = this.parameterRolesForId(parameter.id);
      const lipSync =
        Boolean(this.manifest?.lipSync.enabled) &&
        lipSyncParameterIds.has(parameter.id.toLowerCase());
      if (!roles.length && !lipSync) return [];
      return [
        {
          ...parameter,
          roles,
          lipSync,
        },
      ];
    });
  }

  private parameterRolesForId(id: string): CommonParameterRole[] {
    const normalized = id.toLowerCase();
    return Object.entries(commonLive2DParameterIds).flatMap(([role, ids]) =>
      ids.some((candidate) => candidate.toLowerCase() === normalized)
        ? [role as CommonParameterRole]
        : [],
    );
  }

  private getDrawableBounds(
    coreModel: CubismCoreModelDebugApi,
    index: number,
  ): Live2DDrawableDebugInfo["bounds"] {
    const vertices =
      coreModel.getDrawableVertexPositions?.(index) ??
      coreModel.getDrawableVertices?.(index);
    if (!vertices || vertices.length < 2) return null;

    let minX = Number.POSITIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;

    for (let cursor = 0; cursor < vertices.length - 1; cursor += 2) {
      const x = vertices[cursor];
      const y = vertices[cursor + 1];
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }

    if (![minX, minY, maxX, maxY].every(Number.isFinite)) return null;

    const width = maxX - minX;
    const height = maxY - minY;
    return {
      x: minX,
      y: minY,
      width,
      height,
      area: Math.max(width, 0) * Math.max(height, 0),
    };
  }

  private readIdList(value: unknown): string[] {
    if (!value) return [];
    if (Array.isArray(value)) {
      return value.map((item, index) => this.idToString(item, `#${index}`));
    }
    if (typeof value === "object" && "length" in value) {
      return Array.from(value as ArrayLike<unknown>, (item, index) =>
        this.idToString(item, `#${index}`),
      );
    }
    return [];
  }

  private idAt(ids: string[], index: number, fallback: string): string {
    return ids[index] && ids[index] !== `#${index}` ? ids[index] : fallback;
  }

  private idToString(value: unknown, fallback: string): string {
    if (typeof value === "string") return value;
    if (!value || typeof value !== "object") return fallback;

    const candidate = value as {
      getString?: () => string;
      toString?: () => string;
      _id?: unknown;
      id?: unknown;
      s?: unknown;
    };
    if (typeof candidate.getString === "function") {
      return candidate.getString();
    }
    for (const key of ["_id", "id", "s"] as const) {
      const raw = candidate[key];
      if (typeof raw === "string") return raw;
      if (raw && typeof raw === "object" && "s" in raw) {
        const nested = (raw as { s?: unknown }).s;
        if (typeof nested === "string") return nested;
      }
    }

    const stringified = candidate.toString?.();
    return stringified && stringified !== "[object Object]"
      ? stringified
      : fallback;
  }

  private stringField(value: unknown, keys: string[]): string | null {
    if (!value || typeof value !== "object") return null;
    const record = value as Record<string, unknown>;
    for (const key of keys) {
      const field = record[key];
      if (typeof field === "string" && field.length > 0) return field;
    }
    return null;
  }

  private clamp(value: number, minimum: number, maximum: number): number {
    if (!Number.isFinite(value)) return minimum;
    return Math.min(Math.max(value, minimum), maximum);
  }

  private lerp(start: number, end: number, progress: number): number {
    return start + (end - start) * this.clamp(progress, 0, 1);
  }

  private smoothstep(progress: number): number {
    const clamped = this.clamp(progress, 0, 1);
    return clamped * clamped * (3 - 2 * clamped);
  }
}
