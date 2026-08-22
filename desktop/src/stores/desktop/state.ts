import {
  createDefaultLocalDesktopConfig,
} from "@/domain/config";
import type { DisplayPlan } from "@/domain/displayPlan";
import type { DisplayMotion } from "@/domain/displayPlan";
import type { MotionPlaybackSummary } from "@/domain/motionTelemetry";
import { createInitialPetRuntimeState } from "@/domain/runtime";
import type { PresentationPlan } from "@/domain/runtime";
import type {
  Live2DExpressionOption,
  Live2DMotionOption,
} from "@/domain/live2d";
import {
  availableModelManifests,
  defaultModelManifest,
} from "@/config/live2dModelManifests";
import { mockDesktopDefaults } from "@/config/mockDefaults";
import {
  readPersistedExpressionMaps,
  readPersistedMotionMaps,
} from "@/services/modelMappingStorage";
import { readPersistedTtsSettings } from "@/services/ttsSettingsStorage";
import { readPersistedPomodoroSettings } from "@/services/pomodoroSettingsStorage";
import { idlePomodoroState } from "@/services/pomodoroService";
import type { PomodoroState } from "@/services/pomodoroService";
import type { TurnRecord } from "@/domain/conversation";
import { readPersistedGatewayConnection } from "@/services/gatewayConnectionStorage";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import type { GatewayConnectionStatus } from "@/domain/gatewayConnection";
import { sanitizeGatewayConnectionSettings } from "@/domain/gatewayConnection";
import { withPersistedModelMappings } from "./modelConfig";

export interface TranscriptEntry {
  id: string;
  role: "system" | "user" | "assistant";
  text: string;
  at: string;
  displayPlan?: DisplayPlan;
}

export type PendingAfterEmergeAction =
  | { type: "none" }
  | { type: "presentation" }
  | { type: "error" }
  | { type: "motion"; motion: DisplayMotion };

export interface PendingAfterEmerge {
  enterChat: boolean;
  action: PendingAfterEmergeAction;
}

export function createEmptyPendingAfterEmerge(): PendingAfterEmerge {
  return {
    enterChat: false,
    action: { type: "none" },
  };
}

export interface GatewayPairingState {
  status: "idle" | "exchanging" | "success" | "error";
  message?: string;
}

export function createDesktopState() {
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
  localConfig.pomodoro = readPersistedPomodoroSettings();
  const gatewayConnection: GatewayConnectionSettings =
    sanitizeGatewayConnectionSettings(readPersistedGatewayConnection());

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
    turns: [] as TurnRecord[],
    pendingPresentations: [] as PresentationPlan[],
    recentMotionPlaybacks: [] as MotionPlaybackSummary[],
    activeMotionFeedbackPlaybackId: null as string | null,
    pendingAfterEmerge: createEmptyPendingAfterEmerge(),
    gatewayConnection,
    gatewayConnectionVersion: 0,
    gatewayConnectionStatus: "disconnected" as GatewayConnectionStatus,
    gatewayConnectionError: null as string | null,
    gatewayPairing: { status: "idle" } as GatewayPairingState,
    pomodoroState: { ...idlePomodoroState } as PomodoroState,
    persistenceError: null as string | null,
  };
}
