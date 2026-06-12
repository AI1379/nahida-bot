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
} from "@/domain/runtime";
import type {
  DesktopEvent,
  PetRuntimeState,
  PresentationPlan,
} from "@/domain/runtime";
import {
  petRuntimeNeedsEmerge,
  transitionPetRuntime as reducePetRuntime,
  type PetRuntimeSignal,
} from "@/domain/petRuntimeMachine";
import type { DesktopRuntimeSnapshot } from "@/domain/desktopWindowProtocol";
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
import {
  readPersistedTtsSettings,
  sanitizeTtsSettings,
  writePersistedTtsSettings,
} from "@/services/ttsSettingsStorage";
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

type PendingAfterEmergeAction =
  | { type: "none" }
  | { type: "presentation" }
  | { type: "error" }
  | { type: "motion"; motion: DisplayMotion };

interface PendingAfterEmerge {
  enterChat: boolean;
  action: PendingAfterEmergeAction;
}

function createEmptyPendingAfterEmerge(): PendingAfterEmerge {
  return {
    enterChat: false,
    action: { type: "none" },
  };
}

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
    localConfig.ttsSettings = readPersistedTtsSettings();

    return {
      connected: false,
      gatewayUrl: mockDesktopDefaults.gatewayUrl,
      sessionId: mockDesktopDefaults.sessionId,
      activePlan: null as DisplayPlan | null,
      activePresentation: null as PresentationPlan | null,
      petRuntime,
      localConfig,
      localConfigVersion: 0,
      appliedLocalConfigVersion: -1,
      models,
      expressionOptions: [] as Live2DExpressionOption[],
      motionOptions: [] as Live2DMotionOption[],
      expressionMapVersion: 0,
      motionMapVersion: 0,
      transcript: [] as TranscriptEntry[],
      pendingAfterEmerge: createEmptyPendingAfterEmerge(),
      unsubscribe: null as (() => void) | null,
    };
  },
  getters: {
    currentEmotion: (state) => state.petRuntime.emotion,
    currentExpressionKey: (state) => state.petRuntime.expressionKey,
    currentMotion: (state) => state.petRuntime.motion,
    speaking: (state) => state.petRuntime.speaking,
    currentSegmentIndex: (state) => state.petRuntime.currentSegmentIndex,
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
      this.petRuntime = {
        ...this.petRuntime,
        ...partial,
      };
    },
    transitionPetRuntime(signal: PetRuntimeSignal) {
      const nextRuntime = reducePetRuntime(
        this.petRuntime,
        signal,
        this.localConfig.performanceMode,
      );
      if (nextRuntime === this.petRuntime) return false;
      this.syncPetRuntime(nextRuntime);
      return true;
    },
    requestPetPeek() {
      return this.transitionPetRuntime("peek");
    },
    requestPetEmerge() {
      return this.transitionPetRuntime("emerge");
    },
    commitLocalConfig(localConfig: LocalDesktopConfig) {
      this.localConfig = localConfig;
      this.localConfigVersion += 1;
    },
    clearPendingAfterEmerge() {
      this.pendingAfterEmerge = createEmptyPendingAfterEmerge();
    },
    completePetEmerge() {
      const changed = this.transitionPetRuntime("emerged");
      if (!changed) return false;

      const pending = this.pendingAfterEmerge;
      this.clearPendingAfterEmerge();

      if (pending.action.type === "error") {
        this.transitionPetRuntime("fail");
      } else {
        if (pending.enterChat) {
          this.transitionPetRuntime("enter_chat");
        }
        switch (pending.action.type) {
          case "presentation":
            // The main-window SpeechPlaybackCoordinator observes the
            // now-ready presentation and starts segment playback.
            break;
          case "motion":
            this.applyPreviewMotion(pending.action.motion);
            break;
          case "none":
            break;
        }
      }
      return true;
    },
    requestPetRetreat() {
      const changed = this.transitionPetRuntime("retreat");
      if (changed) this.clearPendingAfterEmerge();
      return changed;
    },
    completePetRetreat() {
      const changed = this.transitionPetRuntime("hide");
      if (changed) this.clearPendingAfterEmerge();
      return changed;
    },
    requestPetHide() {
      const changed = this.transitionPetRuntime("hide");
      if (changed) this.clearPendingAfterEmerge();
      return changed;
    },
    enterPetChat() {
      if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
        this.pendingAfterEmerge = {
          enterChat: true,
          action:
            this.pendingAfterEmerge.action.type === "error"
              ? { type: "none" }
              : this.pendingAfterEmerge.action,
        };
        if (this.petRuntime.status !== "emerging") {
          this.requestPetEmerge();
        }
        return true;
      }
      return this.transitionPetRuntime("enter_chat");
    },
    exitPetChat() {
      if (
        this.petRuntime.status === "emerging" &&
        this.pendingAfterEmerge.enterChat
      ) {
        this.pendingAfterEmerge = {
          ...this.pendingAfterEmerge,
          enterChat: false,
        };
        return true;
      }
      return this.transitionPetRuntime("exit_chat");
    },
    wakePet() {
      if (
        petRuntimeNeedsEmerge(this.petRuntime.status) &&
        this.petRuntime.status !== "emerging"
      ) {
        return this.requestPetEmerge();
      }
      return false;
    },
    queuePresentationStart() {
      this.pendingAfterEmerge = {
        ...this.pendingAfterEmerge,
        action: { type: "presentation" },
      };
      this.wakePet();
    },
    queueErrorAfterEmerge() {
      this.pendingAfterEmerge = {
        enterChat: false,
        action: { type: "error" },
      };
      this.wakePet();
    },
    queueMotionAfterEmerge(motion: DisplayMotion) {
      this.pendingAfterEmerge = {
        ...this.pendingAfterEmerge,
        action: { type: "motion", motion },
      };
      this.wakePet();
    },
    applyPreviewMotion(motion: DisplayMotion) {
      const speaking = motion !== "idle";
      if (speaking) {
        this.transitionPetRuntime("speak");
      } else if (this.petRuntime.status === "speaking") {
        this.transitionPetRuntime("finish_speaking");
      }
      this.syncPetRuntime({
        motion,
        speaking,
        lastEventAt: new Date().toISOString(),
      });
      this.motionMapVersion += 1;
    },
    applyRuntimeSnapshot(snapshot: DesktopRuntimeSnapshot) {
      this.connected = snapshot.connected;
      this.sessionId = snapshot.sessionId;
      this.activePlan = snapshot.activePlan;
      this.activePresentation = snapshot.activePresentation;
      if (
        this.appliedLocalConfigVersion !== snapshot.localConfigVersion
      ) {
        this.localConfig = snapshot.localConfig;
        this.localConfigVersion = snapshot.localConfigVersion;
        this.appliedLocalConfigVersion = snapshot.localConfigVersion;
      }
      this.expressionMapVersion = snapshot.expressionMapVersion;
      this.motionMapVersion = snapshot.motionMapVersion;
      this.syncPetRuntime(snapshot.petRuntime);
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
      this.clearPendingAfterEmerge();
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
    updateTtsSettings(settings: LocalDesktopConfig["ttsSettings"]) {
      const ttsSettings = sanitizeTtsSettings(settings);
      this.commitLocalConfig({
        ...this.localConfig,
        ttsSettings,
      });
      writePersistedTtsSettings(ttsSettings);
    },
    previewSystemSpeech(text: string) {
      const cleanText = text.trim();
      if (!cleanText) return;
      const settings = this.localConfig.ttsSettings;
      this.applyDesktopEvent({
        type: "message.completed",
        source: "local",
        at: new Date().toISOString(),
        sessionId: this.sessionId,
        displayPlan: {
          version: "1.0",
          text: cleanText,
          segments: [
            {
              text: cleanText,
              emotion: "happy",
              motion: "speaking",
              voice: {
                style: "neutral",
                speed: settings.rate,
                pitch: settings.pitch,
              },
            },
          ],
        },
      });
    },
    setSegment(index: number, speaking = true) {
      if (!this.activePlan) return;
      const segment = this.activePlan.segments[index];
      if (!segment) return;
      if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
        this.queuePresentationStart();
        return;
      }
      const expressionKey = segment.expression ?? segment.emotion ?? "neutral";
      const emotion =
        segment.emotion ??
        (isDisplayEmotion(expressionKey) ? expressionKey : "neutral");
      if (speaking) {
        this.transitionPetRuntime("speak");
      } else if (this.petRuntime.speaking) {
        this.transitionPetRuntime("finish_speaking");
      }
      this.syncPetRuntime({
        emotion,
        expressionKey,
        motion: segment.motion ?? (speaking ? "speaking" : "idle"),
        speaking,
        currentSegmentIndex: index,
        activePresentationId: this.activePresentation?.id ?? null,
        bubbleText: segment.text,
        lastEventAt: new Date().toISOString(),
      });
    },
    finishPresentation() {
      if (this.petRuntime.speaking) {
        this.transitionPetRuntime("finish_speaking");
      }
      this.syncPetRuntime({
        motion: "idle",
        speaking: false,
        activePresentationId: null,
        lastEventAt: new Date().toISOString(),
      });
    },
    finishSpeaking() {
      this.finishPresentation();
    },
    selectModel(modelId: string) {
      const model = this.models.find((candidate) => candidate.id === modelId);
      if (!model || model.id === this.selectedModelId) return;
      this.commitLocalConfig({
        ...this.localConfig,
        selectedModelId: model.id,
      });
      this.expressionOptions = [];
      this.motionOptions = [];
      this.expressionMapVersion += 1;
      this.motionMapVersion += 1;
      this.wakePet();
      this.syncPetRuntime({
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
      this.commitLocalConfig(
        withModelConfig(this.localConfig, {
          ...currentConfig,
          expressionMap: nextExpressionMap,
        }),
      );
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
      this.commitLocalConfig(
        withModelConfig(this.localConfig, {
          ...currentConfig,
          expressionMap: nextExpressionMap,
        }),
      );
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
      const emotion = isDisplayEmotion(cleanKeyword)
        ? cleanKeyword
        : "neutral";
      this.wakePet();
      this.syncPetRuntime({
        emotion,
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
      this.commitLocalConfig(
        withModelConfig(this.localConfig, {
          ...currentConfig,
          motionMap: nextMotionMap,
        }),
      );
      writePersistedMotionMap(this.selectedModelId, nextMotionMap);
      this.motionMapVersion += 1;
    },
    previewMotion(motion: DisplayMotion) {
      if (!isDisplayMotion(motion)) return;
      if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
        this.queueMotionAfterEmerge(motion);
        return;
      }
      this.applyPreviewMotion(motion);
    },
    applyDesktopEvent(event: DesktopEvent) {
      switch (event.type) {
        case "connection.changed":
          this.connected = event.connected;
          if (event.connected) {
            if (this.pendingAfterEmerge.action.type === "error") {
              this.pendingAfterEmerge = {
                ...this.pendingAfterEmerge,
                action: { type: "none" },
              };
            }
            this.requestPetEmerge();
            this.syncPetRuntime({
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
            this.clearPendingAfterEmerge();
            if (this.petRuntime.status === "emerging") {
              this.queueErrorAfterEmerge();
            } else {
              this.transitionPetRuntime("fail");
            }
            this.syncPetRuntime({
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
          this.activePlan = null;
          this.activePresentation = null;
          this.clearPendingAfterEmerge();
          if (this.petRuntime.speaking) {
            this.transitionPetRuntime("finish_speaking");
          }
          if (this.petRuntime.status === "hidden") {
            this.requestPetEmerge();
          }
          this.syncPetRuntime({
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
          // Let the emerge slide play before speech starts; speaking
          // immediately would cancel the transition and jump the window.
          if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
            this.queuePresentationStart();
          }
          break;
        }
        case "notification.error": {
          const presentation = presentationPlanFromDesktopEvent(event);
          this.activePresentation = presentation;
          this.activePlan = presentation?.displayPlan ?? this.activePlan;
          if (presentation) {
            if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
              this.queueErrorAfterEmerge();
            } else {
              this.clearPendingAfterEmerge();
              this.transitionPetRuntime("fail");
            }
            this.syncPetRuntime({
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
          this.enterPetChat();
          this.syncPetRuntime({
            lastEventAt: event.at,
          });
          break;
      }
    },
  },
});
