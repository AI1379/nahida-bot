import * as PIXI from "pixi.js";
import { Live2DModel } from "pixi-live2d-display/cubism4";

import type { DisplayEmotion, DisplayMotion } from "@/domain/displayPlan";
import type { Live2DModelManifest } from "@/domain/live2d";

declare global {
  interface Window {
    PIXI?: typeof PIXI;
    Live2DCubismCore?: unknown;
  }
}

export type RenderMode = "suspended" | "idle" | "speaking" | "active";

export interface Live2DRenderer {
  loadModel(manifest: Live2DModelManifest): Promise<void>;
  setExpression(emotion: DisplayEmotion): Promise<void>;
  playMotion(motion: DisplayMotion): Promise<void>;
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

export interface Live2DExpressionDebugInfo {
  index: number;
  name: string;
  file: string;
}

export interface Live2DMotionDebugInfo {
  group: string;
  index: number;
  file: string;
}

export interface Live2DDebugSnapshot {
  modelName: string;
  expressions: Live2DExpressionDebugInfo[];
  motions: Live2DMotionDebugInfo[];
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

const fpsByMode: Record<RenderMode, number> = {
  suspended: 0,
  idle: 15,
  speaking: 30,
  active: 60,
};

export class WebLive2DRenderer implements Live2DRenderer {
  private app: PIXI.Application | null = null;
  private model: Live2DModelInstance | null = null;
  private manifest: Live2DModelManifest | null = null;
  private resizeObserver: ResizeObserver | null = null;
  private readonly host: HTMLElement;
  private readonly parameterOverrides = new Map<number, DebugOverride>();
  private readonly partOpacityOverrides = new Map<number, DebugOverride>();

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
      antialias: false,
      powerPreference: "low-power",
      resolution: Math.min(window.devicePixelRatio || 1, 1.5),
      resizeTo: this.host,
    });

    app.view.className = "live2d-canvas";
    this.host.appendChild(app.view);
    this.app = app;

    const model = await Live2DModel.from(manifest.entry, {
      autoInteract: false,
      autoUpdate: true,
    });
    model.anchor.set(0.5, 0.5);
    model.interactive = false;
    app.stage.addChild(model);
    this.model = model;
    app.ticker.add(this.applyDebugOverrides);

    this.resizeObserver = new ResizeObserver(() => this.fitModel());
    this.resizeObserver.observe(this.host);
    this.fitModel();
    this.setFpsMode("idle");
  }

  async setExpression(emotion: DisplayEmotion): Promise<void> {
    if (!this.model || !this.manifest) return;
    const expression = this.manifest.emotionMap[emotion]?.[0];
    if (!expression) return;
    try {
      await this.model.expression(expression);
    } catch {
      // Some user models have expression files that are not declared or compatible.
    }
  }

  async playMotion(motion: DisplayMotion): Promise<void> {
    if (!this.model || !this.manifest) return;
    const target = this.manifest.motionMap[motion];
    if (!target) return;
    try {
      await this.model.motion(target.group, target.index);
    } catch {
      // The current Nahida test model does not declare motions in model3.json.
    }
  }

  setLipSync(_value: number): void {
    // TODO: wire to Cubism core parameters after TTS audio envelope is available.
  }

  setFpsMode(mode: RenderMode): void {
    if (!this.app) return;
    const fps = fpsByMode[mode];
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

    return {
      modelName: this.manifest.name,
      expressions: this.getExpressionDebugInfo(settings),
      motions: this.getMotionDebugInfo(settings),
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

  async playDebugMotion(group: string, index: number): Promise<void> {
    if (!this.model || !group) return;
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
    this.parameterOverrides.clear();
    this.partOpacityOverrides.clear();

    if (this.model) {
      this.model.destroy({ children: true, texture: true, baseTexture: true });
      this.model = null;
    }

    if (this.app) {
      this.app.destroy(true, { children: true, texture: true, baseTexture: true });
      this.app = null;
    }

    this.host.replaceChildren();
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

  private fitModel(): void {
    if (!this.app || !this.model) return;

    const width = this.host.clientWidth;
    const height = this.host.clientHeight;
    if (width <= 0 || height <= 0) return;

    this.app.renderer.resize(width, height);
    this.model.scale.set(1);
    const modelWidth = Math.max(this.model.width, 1);
    const modelHeight = Math.max(this.model.height, 1);
    const scale = Math.min(width / modelWidth, height / modelHeight) * 0.82;

    this.model.scale.set(scale);
    this.model.position.set(width / 2, height * 0.58);
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

  private getMotionDebugInfo(
    settings: ModelSettingsDebugApi | null,
  ): Live2DMotionDebugInfo[] {
    const groups =
      settings?.motions ?? settings?.json?.FileReferences?.Motions ?? {};

    return Object.entries(groups).flatMap(([group, definitions]) =>
      (definitions ?? []).map((definition, index) => ({
        group,
        index,
        file: this.stringField(definition, ["File", "file"]) ?? "",
      })),
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
}
