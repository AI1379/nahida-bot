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
  modelMappingConfigFromManifest,
} from "@/domain/config";
import type { LocalDesktopConfig } from "@/domain/config";
import {
  sanitizeExpressionKeyword,
  sanitizeExpressionName,
} from "@/domain/displayPlan";
import type {
  DesktopEvent,
  PetRuntimeState,
} from "@/domain/runtime";
import {
  petRuntimeNeedsEmerge,
  transitionPetRuntime as reducePetRuntime,
  type PetRuntimeSignal,
} from "@/domain/petRuntimeMachine";
import type { DesktopRuntimeSnapshot } from "@/domain/desktopWindowProtocol";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import { defaultModelManifest } from "@/config/live2dModelManifests";
import type {
  Live2DExpressionOption,
  Live2DModelManifest,
  Live2DMotionOption,
  Live2DMotionTarget,
} from "@/domain/live2d";
import { clearPersistedModelMappings } from "@/services/modelMappingStorage";
import {
  clearPersistedTtsSettings,
  sanitizeTtsSettings,
} from "@/services/ttsSettingsStorage";
import {
  clearPersistedPomodoroSettings,
  sanitizePomodoroSettings,
} from "@/services/pomodoroSettingsStorage";
import { sanitizeGatewayConnectionSettings } from "@/domain/gatewayConnection";
import { clearPersistedGatewayConnection } from "@/services/gatewayConnectionStorage";
import {
  readDesktopSettings,
  readSecureTokens,
  writeDesktopSettings,
  writeSecureTokens,
} from "@/services/desktopSettingsStorage";
import type { MotionMap } from "@/services/modelMappingStorage";
import { presentationPlanFromDesktopEvent } from "@/services/presentationPlanner";
import {
  nextCustomExpressionKeyword,
  type ExpressionKeywordMap,
  withModelConfig,
} from "./desktop/modelConfig";
import { createDesktopState } from "./desktop/state";
import type { GatewayPairingState, TranscriptEntry } from "./desktop/state";
import { createEmptyPendingAfterEmerge } from "./desktop/state";

export type { TranscriptEntry } from "./desktop/state";

function createTranscriptEntry(
  role: TranscriptEntry["role"],
  text: string,
  at: string,
  displayPlan?: DisplayPlan,
): TranscriptEntry {
  return { id: crypto.randomUUID(), role, text, at, displayPlan };
}

function persistenceErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useDesktopStore = defineStore("desktop", {
  state: createDesktopState,
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
    async hydratePersistentState() {
      const legacyLocalConfig = this.localConfig;
      const legacyGatewayConnection = this.gatewayConnection;
      let persisted = null;

      this.persistenceError = null;
      try {
        persisted = await readDesktopSettings(
          legacyLocalConfig,
          legacyGatewayConnection,
        );
      } catch (error) {
        this.persistenceError = `Could not read desktop settings: ${persistenceErrorMessage(error)}`;
      }

      let secureTokens = {
        nodeToken: legacyGatewayConnection.nodeToken,
        adminBearerToken: legacyGatewayConnection.adminBearerToken,
      };
      try {
        const storedTokens = await readSecureTokens();
        secureTokens = {
          nodeToken: storedTokens.nodeToken || secureTokens.nodeToken,
          adminBearerToken:
            storedTokens.adminBearerToken || secureTokens.adminBearerToken,
        };
      } catch (error) {
        this.persistenceError = `Could not read secure credentials: ${persistenceErrorMessage(error)}`;
      }

      this.localConfig = persisted?.localConfig ?? legacyLocalConfig;
      this.gatewayConnection = sanitizeGatewayConnectionSettings({
        ...(persisted?.gatewayConnection ?? legacyGatewayConnection),
        ...secureTokens,
      });
      this.localConfigVersion += 1;
      this.gatewayConnectionVersion += 1;

      let settingsSaved = false;
      try {
        await writeDesktopSettings(this.localConfig, this.gatewayConnection);
        settingsSaved = true;
      } catch (error) {
        this.persistenceError = `Could not save desktop settings: ${persistenceErrorMessage(error)}`;
      }
      let secureTokensSaved = false;
      try {
        await writeSecureTokens(secureTokens);
        secureTokensSaved = true;
      } catch (error) {
        this.persistenceError = `Could not save secure credentials: ${persistenceErrorMessage(error)}`;
      }

      if (settingsSaved && secureTokensSaved) {
        // Remove the old WebView copies only after both replacements are
        // durable. A transient credential-store failure remains retryable on
        // the next launch instead of silently losing the only token copy.
        clearPersistedGatewayConnection();
        clearPersistedTtsSettings();
        clearPersistedPomodoroSettings();
        clearPersistedModelMappings();
      }
    },
    async persistDesktopSettings() {
      try {
        await writeDesktopSettings(this.localConfig, this.gatewayConnection);
      } catch (error) {
        this.persistenceError = `Could not save desktop settings: ${persistenceErrorMessage(error)}`;
      }
    },
    async persistGatewayTokens() {
      try {
        await writeSecureTokens({
          nodeToken: this.gatewayConnection.nodeToken,
          adminBearerToken: this.gatewayConnection.adminBearerToken,
        });
      } catch (error) {
        this.persistenceError = `Could not save secure credentials: ${persistenceErrorMessage(error)}`;
      }
    },
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
      this.persistenceError = null;
      void this.persistDesktopSettings();
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
    updateTtsSettings(settings: LocalDesktopConfig["ttsSettings"]) {
      const ttsSettings = sanitizeTtsSettings(settings);
      this.commitLocalConfig({
        ...this.localConfig,
        ttsSettings,
      });
    },
    updatePomodoroSettings(settings: LocalDesktopConfig["pomodoro"]) {
      const pomodoro = sanitizePomodoroSettings(settings);
      this.commitLocalConfig({
        ...this.localConfig,
        pomodoro,
      });
    },
    commitGatewayConnection(settings: GatewayConnectionSettings) {
      this.gatewayConnection = sanitizeGatewayConnectionSettings(settings);
      this.gatewayConnectionVersion += 1;
    },
    updateGatewayConnection(patch: Partial<GatewayConnectionSettings>) {
      const tokensChanged =
        (patch.nodeToken !== undefined &&
          patch.nodeToken !== this.gatewayConnection.nodeToken) ||
        (patch.adminBearerToken !== undefined &&
          patch.adminBearerToken !== this.gatewayConnection.adminBearerToken);
      const next: GatewayConnectionSettings = {
        ...this.gatewayConnection,
        ...patch,
      };
      this.commitGatewayConnection(next);
      this.persistenceError = null;
      void this.persistDesktopSettings();
      if (tokensChanged) void this.persistGatewayTokens();
    },
    resetGatewayConnection() {
      this.commitGatewayConnection({
        mode: "mock",
        gatewayWsUrl: "ws://127.0.0.1:6185/api/nodes/ws",
        nodeId: "desktop-local",
        displayName: "Nahida Desktop",
        defaultSessionId: "",
        nodeToken: "",
        adminBearerToken: "",
        ttsSource: "auto",
      });
      this.persistenceError = null;
      void this.persistDesktopSettings();
      void this.persistGatewayTokens();
      this.gatewayConnectionStatus = "disconnected";
      this.gatewayConnectionError = null;
      this.gatewayPairing = { status: "idle" };
    },
    clearGatewayNodeToken() {
      this.updateGatewayConnection({ nodeToken: "" });
      this.gatewayConnectionStatus = "auth-required";
      this.gatewayPairing = { status: "idle" };
    },
    setGatewayConnectionStatus(status: typeof this.gatewayConnectionStatus) {
      this.gatewayConnectionStatus = status;
    },
    setGatewayConnectionError(message: string | null) {
      this.gatewayConnectionError = message;
    },
    setGatewayPairingState(state: GatewayPairingState) {
      this.gatewayPairing = state;
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
          if (event.source === "gateway") {
            this.gatewayConnectionStatus = event.connected
              ? "connected"
              : event.authRequired
                ? "auth-required"
                : "disconnected";
          }
          if (event.gatewayUrl) {
            this.gatewayUrl = event.gatewayUrl;
          }
          if (event.connected) {
            this.gatewayConnectionError = null;
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
            this.transcript.unshift(
              createTranscriptEntry("system",
                event.source === "gateway"
                  ? `Gateway node connected: ${event.nodeId ?? "desktop"}`
                  : "Mock backend connected.",
                event.at,
              ),
            );
          } else {
            this.clearPendingAfterEmerge();
            if (event.reason) {
              this.gatewayConnectionError = event.reason;
            }
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
          this.transcript.unshift(
            createTranscriptEntry("assistant",
              presentation.displayPlan.text,
              event.at,
              presentation.displayPlan,
            ),
          );
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
          this.transcript.unshift(createTranscriptEntry("system", event.message, event.at));
          break;
        }
        case "notification.reminder": {
          const presentation = presentationPlanFromDesktopEvent(event);
          if (!presentation) break;
          if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
            this.activePresentation = presentation;
            this.activePlan = presentation.displayPlan;
            this.queuePresentationStart();
          } else if (this.petRuntime.speaking || this.petRuntime.status === "chat") {
            this.activePresentation = presentation;
            this.activePlan = presentation.displayPlan;
          } else {
            this.activePresentation = presentation;
            this.activePlan = presentation.displayPlan;
            this.transitionPetRuntime("speak");
            this.syncPetRuntime({
              emotion: "happy",
              expressionKey: "happy",
              motion: "notify",
              speaking: false,
              currentSegmentIndex: 0,
              activePresentationId: presentation.id,
              bubbleText: presentation.bubbleText,
              lastEventAt: event.at,
            });
          }
          this.transcript.unshift(createTranscriptEntry("system", event.message, event.at));
          break;
        }
        case "user.message.submitted":
          this.sessionId = event.sessionId;
          this.transcript.unshift(createTranscriptEntry("user", event.text, event.at));
          this.enterPetChat();
          this.syncPetRuntime({
            emotion: "thinking",
            expressionKey: "thinking",
            motion: "idle",
            speaking: false,
            lastEventAt: event.at,
          });
          break;
        case "capability.invoked":
          return this.applyCapabilityInvoke(event.capability, event.arguments);
      }
    },
    applyCapabilityInvoke(
      capability: string,
      args: Record<string, unknown>,
    ) {
      if (capability === "desktop.live2d.set_expression") {
        const expression =
          readStringArg(args.expression) ??
          readStringArg(args.expressionId) ??
          readStringArg(args.expression_id);
        if (!expression) {
          return invalidCapabilityArguments(
            capability,
            "expression must be a non-empty string",
          );
        }
        this.previewExpressionKeyword(expression);
        return capabilityApplied();
      }

      if (capability === "desktop.live2d.play_motion") {
        const motion =
          readStringArg(args.motion) ??
          readStringArg(args.motionId) ??
          readStringArg(args.motion_id);
        if (!isDisplayMotion(motion)) {
          return invalidCapabilityArguments(
            capability,
            "motion must be a supported display motion",
          );
        }
        this.previewMotion(motion);
        return capabilityApplied();
      }

      if (capability === "desktop.notification.show") {
        const message =
          readStringArg(args.message) ??
          readStringArg(args.body) ??
          readStringArg(args.title);
        if (!message) {
          return invalidCapabilityArguments(
            capability,
            "message must be a non-empty string",
          );
        }
        this.transcript.unshift(
          createTranscriptEntry("system", message, new Date().toISOString()),
        );
        return capabilityApplied();
      }

      if (capability === "desktop.notification.announce") {
        const message = readStringArg(args.message);
        if (!message || message.length > maximumAnnouncementLength) {
          return invalidCapabilityArguments(
            capability,
            `message must be a non-empty string of at most ${maximumAnnouncementLength} characters`,
          );
        }
        this.applyDesktopEvent({
          type: "notification.reminder",
          source: "gateway",
          at: new Date().toISOString(),
          message,
          ttsEnabled: true,
        });
        return capabilityApplied();
      }

      return {
        ok: false as const,
        error: {
          code: "capability_not_found",
          message: `Capability ${capability} is not supported by the renderer`,
          retryable: false,
        },
      };
    },
  },
});

function readStringArg(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

const maximumAnnouncementLength = 300;

function capabilityApplied() {
  return { ok: true as const, result: { applied: true } };
}

function invalidCapabilityArguments(capability: string, message: string) {
  return {
    ok: false as const,
    error: {
      code: "invalid_arguments",
      message: `${capability}: ${message}`,
      retryable: false,
    },
  };
}
