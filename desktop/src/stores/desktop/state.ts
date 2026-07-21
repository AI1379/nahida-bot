import {
  createDefaultLocalDesktopConfig,
} from "@/domain/config";
import type { DisplayPlan } from "@/domain/displayPlan";
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
import {
  readPersistedGatewayConnection,
  sanitizeGatewayConnection,
} from "@/services/gatewayConnectionStorage";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";
import { withPersistedModelMappings } from "./modelConfig";
import { createEmptyPendingAfterEmerge } from "./types";
import type { TranscriptEntry } from "./types";

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
    sanitizeGatewayConnection(readPersistedGatewayConnection());

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
    gatewayConnection,
    gatewayConnectionVersion: 0,
    gatewayConnectionError: null as string | null,
    gatewayPairing: { status: "idle" } as GatewayPairingState,
  };
}
