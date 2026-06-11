import { defineStore } from "pinia";

import type {
  DisplayEmotion,
  DisplayMotion,
  DisplayPlan,
} from "@/domain/displayPlan";
import { displayEmotions, displayMotions } from "@/domain/displayPlan";
import {
  createDefaultLocalDesktopConfig,
  modelMappingConfigFromManifest,
} from "@/domain/config";
import type { LocalDesktopConfig } from "@/domain/config";
import {
  createInitialPetRuntimeState,
  renderModeForPerformanceMode,
} from "@/domain/runtime";
import type {
  DesktopEvent,
  PetRuntimeState,
  PresentationPlan,
} from "@/domain/runtime";
import { availableModelManifests, mockModelManifest } from "@/domain/live2d";
import type {
  Live2DExpressionOption,
  Live2DModelManifest,
  Live2DMotionOption,
  Live2DMotionTarget,
} from "@/domain/live2d";
import { mockGatewayEventAdapter } from "@/services/gatewayEventAdapter";
import { mockBackend } from "@/services/mockBackend";
import { presentationPlanFromDesktopEvent } from "@/services/presentationPlanner";

export interface TranscriptEntry {
  id: string;
  role: "system" | "user" | "assistant";
  text: string;
  at: string;
  displayPlan?: DisplayPlan;
}

type ExpressionKeywordMap = Record<string, string[]>;
type PersistedExpressionMaps = Record<string, ExpressionKeywordMap>;
type MotionMap = Partial<Record<DisplayMotion, Live2DMotionTarget>>;
type PersistedMotionMaps = Record<string, MotionMap>;

const expressionMapStorageKey = "nahida.desktop.live2d.expressionMap.v2";
const legacyExpressionMapStorageKey = "nahida.desktop.live2d.emotionMap.v1";
const motionMapStorageKey = "nahida.desktop.live2d.motionMap.v1";

const expressionKeywordPattern = /^[\p{L}\p{N}_.-]+$/u;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isDisplayEmotion(value: string): value is DisplayEmotion {
  return displayEmotions.includes(value as DisplayEmotion);
}

function isDisplayMotion(value: string): value is DisplayMotion {
  return displayMotions.includes(value as DisplayMotion);
}

function cleanExpressionKeyword(value: unknown): string {
  if (typeof value !== "string") return "";
  const keyword = value.trim().slice(0, 48);
  return expressionKeywordPattern.test(keyword) ? keyword : "";
}

function cleanExpressionName(value: unknown): string {
  return typeof value === "string" ? value.trim().slice(0, 160) : "";
}

function readPersistedExpressionMaps(): PersistedExpressionMaps {
  if (typeof window === "undefined") return {};
  try {
    const raw =
      window.localStorage.getItem(expressionMapStorageKey) ??
      window.localStorage.getItem(legacyExpressionMapStorageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed)) return {};

    return Object.fromEntries(
      Object.entries(parsed).map(([modelId, value]) => [
        modelId,
        sanitizeExpressionMap(value),
      ]),
    );
  } catch {
    return {};
  }
}

function writePersistedExpressionMap(
  modelId: string,
  expressionMap: ExpressionKeywordMap,
): void {
  if (typeof window === "undefined") return;
  const persisted = readPersistedExpressionMaps();
  persisted[modelId] = sanitizeExpressionMap(expressionMap);
  window.localStorage.setItem(
    expressionMapStorageKey,
    JSON.stringify(persisted),
  );
}

function sanitizeExpressionMap(value: unknown): ExpressionKeywordMap {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([rawKeyword, raw]) => {
      const keyword = cleanExpressionKeyword(rawKeyword);
      if (!keyword) return [];
      if (Array.isArray(raw)) {
        const expression = cleanExpressionName(raw[0]);
        return [[keyword, expression ? [expression] : []]];
      }
      const expression = cleanExpressionName(raw);
      // raw === "" means the user explicitly cleared the mapping for this keyword
      if (expression || raw === "") return [[keyword, expression ? [expression] : []]];
      return [];
    }),
  ) as ExpressionKeywordMap;
}

function sanitizeMotionTarget(value: unknown): Live2DMotionTarget | null {
  if (!isRecord(value)) return null;
  if (value.source === "none") {
    return { source: "none" };
  }
  if (value.source === "procedural" && typeof value.motion === "string") {
    return isDisplayMotion(value.motion)
      ? { source: "procedural", motion: value.motion }
      : null;
  }

  const source = value.source ?? "model";
  if (
    source === "model" &&
    typeof value.group === "string" &&
    typeof value.index === "number" &&
    Number.isInteger(value.index) &&
    value.index >= 0
  ) {
    return {
      source: "model",
      group: value.group.trim().slice(0, 120),
      index: value.index,
    };
  }

  return null;
}

function sanitizeMotionMap(value: unknown): MotionMap {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([rawMotion, rawTarget]) => {
      if (!isDisplayMotion(rawMotion)) return [];
      const target = sanitizeMotionTarget(rawTarget);
      return target ? [[rawMotion, target]] : [];
    }),
  ) as MotionMap;
}

function readPersistedMotionMaps(): PersistedMotionMaps {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(motionMapStorageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!isRecord(parsed)) return {};

    return Object.fromEntries(
      Object.entries(parsed).map(([modelId, value]) => [
        modelId,
        sanitizeMotionMap(value),
      ]),
    );
  } catch {
    return {};
  }
}

function writePersistedMotionMap(modelId: string, motionMap: MotionMap): void {
  if (typeof window === "undefined") return;
  const persisted = readPersistedMotionMaps();
  persisted[modelId] = sanitizeMotionMap(motionMap);
  window.localStorage.setItem(motionMapStorageKey, JSON.stringify(persisted));
}

function applyPersistedExpressionMap(
  model: Live2DModelManifest,
  persisted: PersistedExpressionMaps,
): Live2DModelManifest {
  return {
    ...model,
    emotionMap: {
      ...model.emotionMap,
      ...(persisted[model.id] ?? {}),
    },
  };
}

function applyPersistedMotionMap(
  model: Live2DModelManifest,
  persisted: PersistedMotionMaps,
): Live2DModelManifest {
  return {
    ...model,
    motionMap: {
      ...model.motionMap,
      ...(persisted[model.id] ?? {}),
    },
  };
}

function withModelMappingConfig(
  config: LocalDesktopConfig,
  model: Live2DModelManifest,
): LocalDesktopConfig {
  const existing =
    config.modelConfigs[model.id] ?? modelMappingConfigFromManifest(model);
  return {
    ...config,
    selectedModelId: model.id,
    modelConfigs: {
      ...config.modelConfigs,
      [model.id]: {
        ...existing,
        expressionMap: { ...model.emotionMap },
        motionMap: { ...model.motionMap },
        lipSync: {
          enabled: model.lipSync.enabled,
          parameterIds: [...model.lipSync.parameterIds],
        },
      },
    },
  };
}

function nextCustomExpressionKeyword(map: ExpressionKeywordMap): string {
  const base = "custom";
  if (!Object.prototype.hasOwnProperty.call(map, base)) return base;
  for (let index = 2; index < 100; index += 1) {
    const candidate = `${base}-${index}`;
    if (!Object.prototype.hasOwnProperty.call(map, candidate)) return candidate;
  }
  return `${base}-${Date.now()}`;
}

export const useDesktopStore = defineStore("desktop", {
  state: () => {
    const persistedExpressions = readPersistedExpressionMaps();
    const persistedMotions = readPersistedMotionMaps();
    const models = availableModelManifests.map((model) =>
      applyPersistedMotionMap(
        applyPersistedExpressionMap(model, persistedExpressions),
        persistedMotions,
      ),
    );
    const fallbackModel = applyPersistedMotionMap(
      applyPersistedExpressionMap(mockModelManifest, persistedExpressions),
      persistedMotions,
    );
    const selectedModel =
      models.find((model) => model.id === mockModelManifest.id) ??
      models[0] ??
      fallbackModel;
    const petRuntime = createInitialPetRuntimeState();

    return {
      connected: false,
      gatewayUrl: "mock://backend",
      sessionId: "desktop:private:mock-user",
      currentEmotion: petRuntime.emotion,
      currentExpressionKey: petRuntime.expressionKey,
      currentMotion: petRuntime.motion,
      speaking: petRuntime.speaking,
      activePlan: null as DisplayPlan | null,
      activePresentation: null as PresentationPlan | null,
      currentSegmentIndex: petRuntime.currentSegmentIndex,
      petRuntime,
      localConfig: createDefaultLocalDesktopConfig(selectedModel),
      models,
      selectedModelId: selectedModel.id,
      model: selectedModel,
      expressionOptions: [] as Live2DExpressionOption[],
      motionOptions: [] as Live2DMotionOption[],
      expressionMapVersion: 0,
      motionMapVersion: 0,
      transcript: [] as TranscriptEntry[],
      unsubscribe: null as (() => void) | null,
    };
  },
  actions: {
    syncPetRuntime(partial: Partial<PetRuntimeState>) {
      const nextRuntime = {
        ...this.petRuntime,
        ...partial,
      };
      this.petRuntime = nextRuntime;
      this.currentEmotion = nextRuntime.emotion;
      this.currentExpressionKey = nextRuntime.expressionKey;
      this.currentMotion = nextRuntime.motion;
      this.speaking = nextRuntime.speaking;
      this.currentSegmentIndex = nextRuntime.currentSegmentIndex;
    },
    startMockBackend() {
      if (this.unsubscribe) return;
      this.unsubscribe = mockBackend.subscribe((event) => {
        const desktopEvent = mockGatewayEventAdapter.toDesktopEvent(event);
        if (desktopEvent) {
          this.applyDesktopEvent(desktopEvent);
        }
      });
      mockBackend.connect();
    },
    stopMockBackend() {
      mockBackend.disconnect();
      this.unsubscribe?.();
      this.unsubscribe = null;
      this.activePlan = null;
      this.activePresentation = null;
    },
    submitUserMessage(text: string) {
      const trimmed = text.trim();
      if (!trimmed) return;
      this.applyDesktopEvent({
        type: "user.message.submitted",
        source: "local",
        at: new Date().toISOString(),
        sessionId: this.sessionId,
        text: trimmed,
      });
      mockBackend.submitUserMessage(trimmed);
    },
    submitMockLlmResult(rawOutput: string) {
      const trimmed = rawOutput.trim();
      if (!trimmed) return;
      mockBackend.submitMockLlmResult(trimmed);
    },
    setSegment(index: number) {
      if (!this.activePlan) return;
      const segment = this.activePlan.segments[index];
      if (!segment) return;
      const expressionKey = segment.expression ?? segment.emotion ?? "neutral";
      const emotion =
        segment.emotion ??
        (isDisplayEmotion(expressionKey) ? expressionKey : "neutral");
      this.syncPetRuntime({
        status: "speaking",
        renderMode: "speaking",
        emotion,
        expressionKey,
        motion: segment.motion ?? "speaking",
        speaking: true,
        currentSegmentIndex: index,
        activePresentationId: this.activePresentation?.id ?? null,
        bubbleText: segment.text,
        lastEventAt: new Date().toISOString(),
      });
    },
    finishSpeaking() {
      this.syncPetRuntime({
        status: "emerged",
        renderMode: renderModeForPerformanceMode(
          this.localConfig.performanceMode,
        ),
        motion: "idle",
        speaking: false,
      });
    },
    selectModel(modelId: string) {
      const model = this.models.find((candidate) => candidate.id === modelId);
      if (!model || model.id === this.selectedModelId) return;
      this.selectedModelId = model.id;
      this.model = model;
      this.expressionOptions = [];
      this.motionOptions = [];
      this.expressionMapVersion += 1;
      this.motionMapVersion += 1;
      this.localConfig = withModelMappingConfig(this.localConfig, model);
      this.syncPetRuntime({
        renderMode: renderModeForPerformanceMode(
          this.localConfig.performanceMode,
        ),
        motion: "idle",
        expressionKey: this.currentEmotion,
        speaking: false,
      });
    },
    setModelExpressions(expressions: Live2DExpressionOption[]) {
      this.expressionOptions = expressions;
    },
    setModelMotions(motions: Live2DMotionOption[]) {
      this.motionOptions = motions;
    },
    setExpressionKeywordMapping(
      keyword: string,
      nextKeyword: string,
      expressionName: string,
    ) {
      const cleanKeyword = cleanExpressionKeyword(nextKeyword);
      if (!cleanKeyword) return;

      const previousKeyword = cleanExpressionKeyword(keyword);
      const nextExpressionMap: ExpressionKeywordMap = {
        ...this.model.emotionMap,
      };
      if (previousKeyword && previousKeyword !== cleanKeyword) {
        delete nextExpressionMap[previousKeyword];
      }

      const expression = cleanExpressionName(expressionName);
      if (expression) {
        nextExpressionMap[cleanKeyword] = [expression];
      } else {
        nextExpressionMap[cleanKeyword] = [];
      }

      const nextModel: Live2DModelManifest = {
        ...this.model,
        emotionMap: nextExpressionMap,
      };
      this.model = nextModel;
      this.models = this.models.map((model) =>
        model.id === nextModel.id ? nextModel : model,
      );
      this.localConfig = withModelMappingConfig(this.localConfig, nextModel);
      writePersistedExpressionMap(nextModel.id, nextExpressionMap);
      if (this.currentExpressionKey === previousKeyword) {
        this.syncPetRuntime({ expressionKey: cleanKeyword });
      }
      this.expressionMapVersion += 1;
    },
    addExpressionKeywordMapping() {
      const keyword = nextCustomExpressionKeyword(this.model.emotionMap);
      this.setExpressionKeywordMapping(keyword, keyword, "");
    },
    removeExpressionKeywordMapping(keyword: string) {
      const cleanKeyword = cleanExpressionKeyword(keyword);
      if (!cleanKeyword) return;
      const nextExpressionMap: ExpressionKeywordMap = {
        ...this.model.emotionMap,
      };
      delete nextExpressionMap[cleanKeyword];

      const nextModel: Live2DModelManifest = {
        ...this.model,
        emotionMap: nextExpressionMap,
      };
      this.model = nextModel;
      this.models = this.models.map((model) =>
        model.id === nextModel.id ? nextModel : model,
      );
      this.localConfig = withModelMappingConfig(this.localConfig, nextModel);
      writePersistedExpressionMap(nextModel.id, nextExpressionMap);
      if (this.currentExpressionKey === cleanKeyword) {
        this.syncPetRuntime({ expressionKey: this.currentEmotion });
      }
      this.expressionMapVersion += 1;
    },
    setEmotionExpression(emotion: DisplayEmotion, expressionName: string) {
      this.setExpressionKeywordMapping(emotion, emotion, expressionName);
    },
    previewExpressionKeyword(keyword: string) {
      const cleanKeyword = cleanExpressionKeyword(keyword);
      if (!cleanKeyword) return;
      this.currentEmotion = isDisplayEmotion(cleanKeyword)
        ? cleanKeyword
        : "neutral";
      this.syncPetRuntime({
        status: "emerged",
        renderMode: renderModeForPerformanceMode(
          this.localConfig.performanceMode,
        ),
        emotion: this.currentEmotion,
        expressionKey: cleanKeyword,
        motion: "idle",
        speaking: false,
        lastEventAt: new Date().toISOString(),
      });
      this.expressionMapVersion += 1;
    },
    previewEmotion(emotion: DisplayEmotion) {
      this.previewExpressionKeyword(emotion);
    },
    setMotionMapping(motion: DisplayMotion, target: Live2DMotionTarget) {
      if (!displayMotions.includes(motion)) return;
      const nextMotionMap: MotionMap = {
        ...this.model.motionMap,
        [motion]: target,
      };

      const nextModel: Live2DModelManifest = {
        ...this.model,
        motionMap: nextMotionMap,
      };
      this.model = nextModel;
      this.models = this.models.map((model) =>
        model.id === nextModel.id ? nextModel : model,
      );
      this.localConfig = withModelMappingConfig(this.localConfig, nextModel);
      writePersistedMotionMap(nextModel.id, nextMotionMap);
      this.motionMapVersion += 1;
    },
    previewMotion(motion: DisplayMotion) {
      if (!displayMotions.includes(motion)) return;
      const speaking = motion !== "idle";
      this.syncPetRuntime({
        status: speaking ? "speaking" : "emerged",
        renderMode: renderModeForPerformanceMode(
          this.localConfig.performanceMode,
          speaking,
        ),
        motion,
        speaking,
        lastEventAt: new Date().toISOString(),
      });
      this.motionMapVersion += 1;
    },
    applyDesktopEvent(event: DesktopEvent) {
      switch (event.type) {
        case "connection.changed":
          this.connected = event.connected;
          if (event.connected) {
            this.syncPetRuntime({
              status: "emerged",
              renderMode: renderModeForPerformanceMode(
                this.localConfig.performanceMode,
              ),
              emotion: "happy",
              expressionKey: "happy",
              motion: "idle",
              speaking: false,
              lastEventAt: event.at,
            });
            this.transcript.unshift({
              id: crypto.randomUUID(),
              role: "system",
              text: "Mock backend connected.",
              at: event.at,
            });
          } else {
            this.syncPetRuntime({
              status: "error",
              renderMode: renderModeForPerformanceMode(
                this.localConfig.performanceMode,
              ),
              emotion: "offline",
              expressionKey: "offline",
              motion: "idle",
              speaking: false,
              lastEventAt: event.at,
            });
          }
          break;
        case "message.started":
          this.sessionId = event.sessionId;
          this.syncPetRuntime({
            status: "emerged",
            renderMode: renderModeForPerformanceMode(
              this.localConfig.performanceMode,
            ),
            emotion: "thinking",
            expressionKey: "thinking",
            motion: "idle",
            speaking: false,
            lastEventAt: event.at,
          });
          break;
        case "message.completed": {
          const presentation = presentationPlanFromDesktopEvent(event);
          if (!presentation) return;
          this.sessionId = event.sessionId;
          this.activePresentation = presentation;
          this.activePlan = presentation.displayPlan;
          this.transcript.unshift({
            id: crypto.randomUUID(),
            role: "assistant",
            text: presentation.displayPlan.text,
            at: event.at,
            displayPlan: presentation.displayPlan,
          });
          this.setSegment(0);
          break;
        }
        case "notification.error": {
          const presentation = presentationPlanFromDesktopEvent(event);
          this.activePresentation = presentation;
          this.activePlan = presentation?.displayPlan ?? this.activePlan;
          if (presentation) {
            this.syncPetRuntime({
              status: "error",
              renderMode: renderModeForPerformanceMode(
                this.localConfig.performanceMode,
              ),
              emotion: "error",
              expressionKey: "error",
              motion: "idle",
              speaking: false,
              currentSegmentIndex: 0,
              activePresentationId: presentation.id,
              bubbleText: presentation.bubbleText,
              lastEventAt: event.at,
            });
          }
          this.transcript.unshift({
            id: crypto.randomUUID(),
            role: "system",
            text: event.message,
            at: event.at,
          });
          break;
        }
        case "user.message.submitted":
          this.sessionId = event.sessionId;
          this.transcript.unshift({
            id: crypto.randomUUID(),
            role: "user",
            text: event.text,
            at: event.at,
          });
          this.syncPetRuntime({
            status: "chat",
            renderMode: "active",
            lastEventAt: event.at,
          });
          break;
      }
    },
  },
});
