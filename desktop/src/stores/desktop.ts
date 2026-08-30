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
  sanitizeModelPerformanceProfile,
  withLocalModelPerformanceProfileVersion,
  type ModelPerformanceProfile,
} from "@/domain/modelPerformanceProfile";
import {
  sanitizeExpressionKeyword,
  sanitizeExpressionName,
} from "@/domain/displayPlan";
import type {
  DesktopEvent,
  PetRuntimeState,
  PresentationPlan,
} from "@/domain/runtime";
import { renderModeForPerformanceMode } from "@/domain/runtime";
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
import type { PomodoroState } from "@/services/pomodoroService";
import { sanitizeGatewayConnectionSettings } from "@/domain/gatewayConnection";
import { clearPersistedGatewayConnection } from "@/services/gatewayConnectionStorage";
import {
  readDesktopSettings,
  readSecureTokens,
  sanitizePetTriggerSettings,
  writeDesktopSettings,
  writeSecureTokens,
} from "@/services/desktopSettingsStorage";
import type { MotionMap } from "@/services/modelMappingStorage";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import type { TurnRecord, TurnStatus } from "@/domain/conversation";
import { mergeRecentMotionPlaybacks } from "@/services/motionPlaybackHistory";
import {
  readConversationHistory,
  writeConversationHistory,
} from "@/services/conversationStorage";
import { presentationPlanFromDesktopEvent } from "@/services/presentationPlanner";
import { showDesktopNotification } from "@/services/desktopNotification";
import {
  nextCustomExpressionKeyword,
  type ExpressionKeywordMap,
  withModelConfig,
} from "./desktop/modelConfig";
import { createDesktopState } from "./desktop/state";
import type {
  GatewayPairingState,
  TranscriptEntry,
} from "./desktop/state";
import { createEmptyPendingAfterEmerge } from "./desktop/state";

export type { TranscriptEntry } from "./desktop/state";
export type { TurnRecord, TurnStatus } from "@/domain/conversation";

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
    currentSessionTurns(state): TurnRecord[] {
      return state.turns.filter((turn) => turn.sessionId === state.sessionId);
    },
    latestMotionFeedbackPlayback(state): MotionPlaybackSummary | null {
      return state.recentMotionPlaybacks.find(
        (playback) =>
          playback.motionPlanId === state.activeMotionFeedbackPlaybackId,
      ) ?? null;
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

      try {
        this.turns = await readConversationHistory();
      } catch (error) {
        this.persistenceError = `Could not read conversation history: ${persistenceErrorMessage(error)}`;
      }

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
    persistConversationHistory() {
      void writeConversationHistory(this.turns).catch((error) => {
        this.persistenceError = `Could not save conversation history: ${persistenceErrorMessage(error)}`;
      });
    },
    beginUserTurn(
      text: string,
      sessionId: string,
      at = new Date().toISOString(),
    ): TurnRecord {
      const turn: TurnRecord = {
        id: crypto.randomUUID(),
        sessionId,
        userText: text,
        assistantText: "",
        status: "submitting",
        createdAt: at,
        updatedAt: at,
      };
      this.turns.unshift(turn);
      this.turns = this.turns.slice(0, 200);
      this.transcript.unshift(createTranscriptEntry("user", text, at));
      this.persistConversationHistory();
      return turn;
    },
    updateTurn(
      turnId: string,
      patch: Partial<
        Pick<
          TurnRecord,
          "sessionId" | "assistantText" | "status" | "presentationId" | "error"
        >
      >,
    ): TurnRecord | null {
      const turn = this.turns.find((candidate) => candidate.id === turnId);
      if (!turn) return null;
      Object.assign(turn, patch, { updatedAt: new Date().toISOString() });
      this.persistConversationHistory();
      return turn;
    },
    markTurnAccepted(turnId: string) {
      const turn = this.turns.find((candidate) => candidate.id === turnId);
      if (!turn || turn.status !== "submitting") return turn ?? null;
      return this.updateTurn(turnId, { status: "accepted", error: undefined });
    },
    markTurnFailed(turnId: string, error: string) {
      return this.updateTurn(turnId, { status: "failed", error });
    },
    failPendingGatewayTurns(error: string) {
      let changed = false;
      const updatedAt = new Date().toISOString();
      for (const turn of this.turns) {
        if (
          turn.status !== "submitting" &&
          turn.status !== "accepted" &&
          turn.status !== "generating"
        ) continue;
        turn.status = "failed";
        turn.error = error;
        turn.updatedAt = updatedAt;
        changed = true;
      }
      if (changed) this.persistConversationHistory();
    },
    findPendingTurn(
      sessionId: string,
      statuses: readonly TurnStatus[],
    ): TurnRecord | null {
      const allowed = new Set(statuses);
      for (let index = this.turns.length - 1; index >= 0; index -= 1) {
        const turn = this.turns[index];
        if (turn?.sessionId === sessionId && allowed.has(turn.status)) return turn;
      }
      return null;
    },
    markNextTurnGenerating(sessionId: string): TurnRecord | null {
      const turn = this.findPendingTurn(sessionId, ["submitting", "accepted"]);
      return turn ? this.updateTurn(turn.id, { status: "generating" }) : null;
    },
    receiveAssistantTurn(
      sessionId: string,
      assistantText: string,
      presentationId: string,
      ttsEnabled: boolean,
      at: string,
    ): TurnRecord {
      const pending = this.findPendingTurn(sessionId, [
        "generating",
        "accepted",
        "submitting",
      ]);
      if (pending) {
        return this.updateTurn(pending.id, {
          assistantText,
          presentationId,
          status: ttsEnabled ? "synthesizing" : "playing",
          error: undefined,
        })!;
      }
      const turn: TurnRecord = {
        id: crypto.randomUUID(),
        sessionId,
        userText: "",
        assistantText,
        status: ttsEnabled ? "synthesizing" : "playing",
        presentationId,
        createdAt: at,
        updatedAt: at,
      };
      this.turns.unshift(turn);
      this.persistConversationHistory();
      return turn;
    },
    markPresentationTurn(
      presentationId: string,
      status: TurnStatus,
      error?: string,
    ) {
      const turn = this.turns.find(
        (candidate) => candidate.presentationId === presentationId,
      );
      return turn
        ? this.updateTurn(turn.id, { status, error })
        : null;
    },
    enqueuePresentation(presentation: PresentationPlan) {
      if (presentation.interruption === "replace") {
        this.pendingPresentations = [];
      }
      if (
        presentation.dedupeKey &&
        (this.activePresentation?.dedupeKey === presentation.dedupeKey ||
          this.pendingPresentations.some(
            (candidate) => candidate.dedupeKey === presentation.dedupeKey,
          ))
      ) {
        return false;
      }
      this.pendingPresentations.push(presentation);
      return true;
    },
    takePendingPresentations(): PresentationPlan[] {
      const pending = [...this.pendingPresentations];
      this.pendingPresentations = [];
      return pending;
    },
    startPresentation(presentation: PresentationPlan) {
      this.activePresentation = presentation;
      this.activePlan = presentation.displayPlan;
    },
    clearPresentations() {
      this.pendingPresentations = [];
      this.activePresentation = null;
      this.activePlan = null;
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
      if (changed) {
        if (this.pendingAfterEmerge.action.type === "presentation") {
          this.pendingPresentations = [];
        }
        this.clearPendingAfterEmerge();
      }
      return changed;
    },
    completePetRetreat() {
      const changed = this.transitionPetRuntime("hide");
      if (changed) this.clearPendingAfterEmerge();
      return changed;
    },
    requestPetHide() {
      const changed = this.transitionPetRuntime("hide");
      if (changed) {
        if (this.pendingAfterEmerge.action.type === "presentation") {
          this.pendingPresentations = [];
        }
        this.clearPendingAfterEmerge();
      }
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
      this.clearActiveMotionFeedbackCandidate();
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
      if (snapshot.turns) this.turns = snapshot.turns;
      if (snapshot.activeMotionFeedbackPlaybackId !== undefined) {
        this.activeMotionFeedbackPlaybackId =
          snapshot.activeMotionFeedbackPlaybackId;
      }
      if (snapshot.pomodoro) this.pomodoroState = snapshot.pomodoro;
      this.syncPetRuntime(snapshot.petRuntime);
    },
    updateTtsSettings(settings: LocalDesktopConfig["ttsSettings"]) {
      const ttsSettings = sanitizeTtsSettings(settings);
      this.commitLocalConfig({
        ...this.localConfig,
        ttsSettings,
      });
    },
    rememberMotionPlayback(playback: MotionPlaybackSummary) {
      this.recentMotionPlaybacks = mergeRecentMotionPlaybacks(
        this.recentMotionPlaybacks,
        [playback],
      );
      this.activeMotionFeedbackPlaybackId = playback.motionPlanId;
    },
    clearActiveMotionFeedbackCandidate() {
      this.activeMotionFeedbackPlaybackId = null;
    },
    mergeRecentMotionPlaybackHistory(playbacks: MotionPlaybackSummary[]) {
      this.recentMotionPlaybacks = mergeRecentMotionPlaybacks(
        this.recentMotionPlaybacks,
        playbacks,
      );
    },
    updateMotionDataCollectionEnabled(enabled: boolean) {
      this.commitLocalConfig({
        ...this.localConfig,
        motionDataCollectionEnabled: enabled,
      });
    },
    updatePomodoroSettings(settings: LocalDesktopConfig["pomodoro"]) {
      const pomodoro = sanitizePomodoroSettings(settings);
      this.commitLocalConfig({
        ...this.localConfig,
        pomodoro,
      });
    },
    setPomodoroState(state: PomodoroState) {
      this.pomodoroState = state;
    },
    updatePetTriggerSettings(settings: LocalDesktopConfig["petTriggers"]) {
      const petTriggers = sanitizePetTriggerSettings(
        settings,
        this.localConfig.petTriggers,
      );
      this.commitLocalConfig({
        ...this.localConfig,
        petTriggers,
      });
    },
    updateDesktopWindowState(
      patch: Partial<LocalDesktopConfig["windowState"]>,
    ) {
      const current = this.localConfig.windowState;
      const width = Number.isFinite(patch.width)
        ? Math.min(720, Math.max(280, Number(patch.width)))
        : current.width;
      const height = Number.isFinite(patch.height)
        ? Math.min(900, Math.max(360, Number(patch.height)))
        : current.height;
      const exposedPx = Number.isFinite(patch.exposedPx)
        ? Math.min(160, Math.max(16, Number(patch.exposedPx)))
        : current.exposedPx;
      this.commitLocalConfig({
        ...this.localConfig,
        windowState: {
          ...current,
          ...patch,
          width,
          height,
          exposedPx,
        },
      });
    },
    updatePerformanceMode(mode: LocalDesktopConfig["performanceMode"]) {
      if (!(["power_saver", "balanced", "active"] as const).includes(mode)) {
        return;
      }
      this.commitLocalConfig({
        ...this.localConfig,
        performanceMode: mode,
      });
      if (this.petRuntime.status !== "hidden") {
        this.syncPetRuntime({
          renderMode:
            this.petRuntime.status === "chat"
              ? "active"
              : renderModeForPerformanceMode(mode, this.petRuntime.speaking),
        });
      }
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
        mode: "gateway",
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
    setSegment(index: number, speaking = true, durationMs?: number) {
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
        segmentDurationMs:
          typeof durationMs === "number" && Number.isFinite(durationMs)
            ? Math.max(0, durationMs)
            : null,
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
        segmentDurationMs: null,
        activePresentationId: null,
        bubbleText: "",
        lastEventAt: new Date().toISOString(),
      });
      this.activePresentation = null;
      this.activePlan = null;
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
    updateModelPerformanceProfile(profile: ModelPerformanceProfile) {
      const currentConfig =
        this.localConfig.modelConfigs[this.selectedModelId] ??
        modelMappingConfigFromManifest(this.model);
      const performanceProfile = withLocalModelPerformanceProfileVersion(
        sanitizeModelPerformanceProfile(
          profile,
          currentConfig.performanceProfile,
        ),
      );
      this.commitLocalConfig(
        withModelConfig(this.localConfig, {
          ...currentConfig,
          performanceProfile,
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
            this.failPendingGatewayTurns(
              event.reason || "The connection closed before the reply completed.",
            );
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
          this.markNextTurnGenerating(event.sessionId);
          this.clearPendingAfterEmerge();
          if (this.petRuntime.status === "hidden") {
            this.requestPetEmerge();
          }
          if (!this.petRuntime.speaking) {
            this.syncPetRuntime({
              emotion: "thinking",
              expressionKey: "thinking",
              motion: "idle",
              speaking: false,
              lastEventAt: event.at,
            });
          }
          break;
        case "message.completed": {
          const plannedPresentation = presentationPlanFromDesktopEvent(event);
          if (!plannedPresentation) return;
          this.sessionId = event.sessionId;
          this.clearActiveMotionFeedbackCandidate();
          const turn = this.receiveAssistantTurn(
            event.sessionId,
            plannedPresentation.displayPlan.text,
            plannedPresentation.id,
            plannedPresentation.ttsEnabled,
            event.at,
          );
          const presentation = { ...plannedPresentation, turnId: turn.id };
          this.enqueuePresentation(presentation);
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
          void showDesktopNotification({
            title: "Nahida · 错误",
            body: event.message,
          });
          this.clearActiveMotionFeedbackCandidate();
          const presentation = presentationPlanFromDesktopEvent(event);
          if (presentation) {
            this.enqueuePresentation(presentation);
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
          void showDesktopNotification({
            title: "Nahida · 提醒",
            body: event.message,
          });
          this.clearActiveMotionFeedbackCandidate();
          const presentation = presentationPlanFromDesktopEvent(event);
          if (!presentation) break;
          if (petRuntimeNeedsEmerge(this.petRuntime.status)) {
            this.enqueuePresentation(presentation);
            this.queuePresentationStart();
          } else if (this.petRuntime.speaking || this.petRuntime.status === "chat") {
            this.enqueuePresentation(presentation);
          } else {
            this.enqueuePresentation(presentation);
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
          if (
            !this.turns.some(
              (turn) =>
                turn.sessionId === event.sessionId &&
                turn.userText === event.text &&
                turn.createdAt === event.at,
            )
          ) {
            this.beginUserTurn(event.text, event.sessionId, event.at);
          }
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
        const title = readStringArg(args.title) ?? "Nahida Desktop";
        const message =
          readStringArg(args.message) ??
          readStringArg(args.body) ??
          title;
        if (!message) {
          return invalidCapabilityArguments(
            capability,
            "message must be a non-empty string",
          );
        }
        this.transcript.unshift(
          createTranscriptEntry("system", message, new Date().toISOString()),
        );
        void showDesktopNotification({ title, body: message });
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
