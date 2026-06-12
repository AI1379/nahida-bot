import { defineStore } from "pinia";

import type {
  DisplayEmotion,
  DisplayMotion,
  DisplayPlan,
} from "@/domain/displayPlan";
import {
  isDisplayEmotion,
  isDisplayMotion,
} from "@/domain/displayPlan";
import {
  configuredModelFromManifest,
  createDefaultLocalDesktopConfig,
  modelMappingConfigFromManifest,
} from "@/domain/config";
import type {
  LocalDesktopConfig,
  ModelMappingConfig,
} from "@/domain/config";
import {
  sanitizeExpressionKeyword,
  sanitizeExpressionName,
} from "@/domain/displayPlanPolicy";
import {
  createInitialPetRuntimeState,
  renderModeForPerformanceMode,
} from "@/domain/runtime";
import type {
  DesktopEvent,
  PetRuntimeState,
  PresentationPlan,
} from "@/domain/runtime";
import {
  availableModelManifests,
  defaultModelManifest,
} from "@/config/live2dModelManifests";
import { mockDesktopDefaults } from "@/config/mockDefaults";
import type {
  Live2DExpressionMap,
  Live2DExpressionOption,
  Live2DModelManifest,
  Live2DMotionOption,
  Live2DMotionTarget,
} from "@/domain/live2d";
import { mockGatewayEventAdapter } from "@/services/gatewayEventAdapter";
import { mockBackend } from "@/services/mockBackend";
import {
  readPersistedExpressionMaps,
  readPersistedMotionMaps,
  writePersistedExpressionMap,
  writePersistedMotionMap,
} from "@/services/modelMappingStorage";
import type {
  MotionMap,
  PersistedExpressionMaps,
  PersistedMotionMaps,
} from "@/services/modelMappingStorage";
import { presentationPlanFromDesktopEvent } from "@/services/presentationPlanner";

export interface TranscriptEntry {
  id: string;
  role: "system" | "user" | "assistant";
  text: string;
  at: string;
  displayPlan?: DisplayPlan;
}

type ExpressionKeywordMap = Live2DExpressionMap;

function withPersistedModelMappings(
  config: LocalDesktopConfig,
  persistedExpressions: PersistedExpressionMaps,
  persistedMotions: PersistedMotionMaps,
): LocalDesktopConfig {
  return {
    ...config,
    modelConfigs: Object.fromEntries(
      Object.entries(config.modelConfigs).map(([modelId, modelConfig]) => [
        modelId,
        {
          ...modelConfig,
          expressionMap: {
            ...modelConfig.expressionMap,
            ...(persistedExpressions[modelId] ?? {}),
          },
          motionMap: {
            ...modelConfig.motionMap,
            ...(persistedMotions[modelId] ?? {}),
          },
        },
      ]),
    ),
  };
}

function withModelConfig(
  config: LocalDesktopConfig,
  modelConfig: ModelMappingConfig,
): LocalDesktopConfig {
  return {
    ...config,
    modelConfigs: {
      ...config.modelConfigs,
      [modelConfig.modelId]: modelConfig,
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
    const models = availableModelManifests;
    const selectedModel =
      models.find((model) => model.id === defaultModelManifest.id) ??
      models[0] ??
      defaultModelManifest;
    const petRuntime = createInitialPetRuntimeState();
    const localConfig = withPersistedModelMappings(
      createDefaultLocalDesktopConfig(selectedModel, models),
      persistedExpressions,
      persistedMotions,
    );

    return {
      connected: false,
      gatewayUrl: mockDesktopDefaults.gatewayUrl,
      sessionId: mockDesktopDefaults.sessionId,
      currentEmotion: petRuntime.emotion,
      currentExpressionKey: petRuntime.expressionKey,
      currentMotion: petRuntime.motion,
      speaking: petRuntime.speaking,
      activePlan: null as DisplayPlan | null,
      activePresentation: null as PresentationPlan | null,
      currentSegmentIndex: petRuntime.currentSegmentIndex,
      petRuntime,
      localConfig,
      models,
      expressionOptions: [] as Live2DExpressionOption[],
      motionOptions: [] as Live2DMotionOption[],
      expressionMapVersion: 0,
      motionMapVersion: 0,
      transcript: [] as TranscriptEntry[],
      unsubscribe: null as (() => void) | null,
    };
  },
  getters: {
    selectedModelId: (state): string => state.localConfig.selectedModelId,
    model(state): Live2DModelManifest {
      const manifest =
        state.models.find(
          (candidate) => candidate.id === state.localConfig.selectedModelId,
        ) ??
        state.models[0] ??
        defaultModelManifest;
      const modelConfig =
        state.localConfig.modelConfigs[manifest.id] ??
        modelMappingConfigFromManifest(manifest);
      return configuredModelFromManifest(manifest, modelConfig);
    },
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
      this.localConfig = {
        ...this.localConfig,
        selectedModelId: model.id,
      };
      this.expressionOptions = [];
      this.motionOptions = [];
      this.expressionMapVersion += 1;
      this.motionMapVersion += 1;
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
      const cleanKeyword = sanitizeExpressionKeyword(nextKeyword);
      if (!cleanKeyword) return;

      const previousKeyword = sanitizeExpressionKeyword(keyword);
      const nextExpressionMap: ExpressionKeywordMap = {
        ...this.model.emotionMap,
      };
      if (previousKeyword && previousKeyword !== cleanKeyword) {
        delete nextExpressionMap[previousKeyword];
      }

      const expression = sanitizeExpressionName(expressionName);
      if (expression) {
        nextExpressionMap[cleanKeyword] = [expression];
      } else {
        nextExpressionMap[cleanKeyword] = [];
      }

      const currentConfig =
        this.localConfig.modelConfigs[this.selectedModelId] ??
        modelMappingConfigFromManifest(this.model);
      this.localConfig = withModelConfig(this.localConfig, {
        ...currentConfig,
        expressionMap: nextExpressionMap,
      });
      writePersistedExpressionMap(this.selectedModelId, nextExpressionMap);
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
      const cleanKeyword = sanitizeExpressionKeyword(keyword);
      if (!cleanKeyword) return;
      const nextExpressionMap: ExpressionKeywordMap = {
        ...this.model.emotionMap,
      };
      delete nextExpressionMap[cleanKeyword];

      const currentConfig =
        this.localConfig.modelConfigs[this.selectedModelId] ??
        modelMappingConfigFromManifest(this.model);
      this.localConfig = withModelConfig(this.localConfig, {
        ...currentConfig,
        expressionMap: nextExpressionMap,
      });
      writePersistedExpressionMap(this.selectedModelId, nextExpressionMap);
      if (this.currentExpressionKey === cleanKeyword) {
        this.syncPetRuntime({ expressionKey: this.currentEmotion });
      }
      this.expressionMapVersion += 1;
    },
    setEmotionExpression(emotion: DisplayEmotion, expressionName: string) {
      this.setExpressionKeywordMapping(emotion, emotion, expressionName);
    },
    previewExpressionKeyword(keyword: string) {
      const cleanKeyword = sanitizeExpressionKeyword(keyword);
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
      if (!isDisplayMotion(motion)) return;
      const nextMotionMap: MotionMap = {
        ...this.model.motionMap,
        [motion]: target,
      };

      const currentConfig =
        this.localConfig.modelConfigs[this.selectedModelId] ??
        modelMappingConfigFromManifest(this.model);
      this.localConfig = withModelConfig(this.localConfig, {
        ...currentConfig,
        motionMap: nextMotionMap,
      });
      writePersistedMotionMap(this.selectedModelId, nextMotionMap);
      this.motionMapVersion += 1;
    },
    previewMotion(motion: DisplayMotion) {
      if (!isDisplayMotion(motion)) return;
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
