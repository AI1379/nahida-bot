import {
  computed,
  onBeforeUnmount,
  onMounted,
  watch,
} from "vue";
import type { UnlistenFn } from "@tauri-apps/api/event";

import { desktopWindowDefaults } from "@/config/desktopRuntimeDefaults";
import type {
  DesktopRuntimeSnapshot,
  PetWindowCommand,
} from "@/domain/desktopWindowProtocol";
import { petRuntimeNeedsEmerge } from "@/domain/petRuntimeMachine";
import {
  listenForPetCommands,
  publishRuntimeSnapshot,
} from "@/services/desktopWindowBridge";
import { SpeechPlaybackCoordinator } from "@/services/speechPlaybackCoordinator";
import { SystemSpeechAdapter } from "@/services/systemSpeechAdapter";
import {
  completeGatewayPairing,
  pairDevice,
  type PairDeviceResult,
  type PairingCompleteResult,
} from "@/services/gatewayPairing";
import {
  createDesktopEventSource,
  type DesktopEventSource,
  type DesktopEventSourceOptions,
} from "@/runtime/desktopEventSource";
import type { useDesktopStore } from "@/stores/desktop";

type DesktopStore = ReturnType<typeof useDesktopStore>;

export interface DesktopRuntimeActions {
  connectMockBackend(): void;
  disconnectMockBackend(): void;
  connectGateway(): void;
  disconnectGateway(): void;
  reconnectGateway(): void;
  exchangePairingToken(token: string): Promise<PairingCompleteResult>;
  pairDevice(adminBearerToken?: string): Promise<PairDeviceResult>;
  submitUserMessage(text: string): void;
  submitMockLlmResult(rawOutput: string): void;
}

function clearTimer(timer: ReturnType<typeof setTimeout> | null) {
  if (timer !== null) clearTimeout(timer);
}

export function useDesktopRuntimeController(
  store: DesktopStore,
  initialEventSource?: DesktopEventSource,
): DesktopRuntimeActions {
  const runtimeSnapshot = computed<DesktopRuntimeSnapshot>(() => ({
    connected: store.connected,
    sessionId: store.sessionId,
    activePlan: store.activePlan,
    activePresentation: store.activePresentation,
    petRuntime: store.petRuntime,
    localConfig: store.localConfig,
    localConfigVersion: store.localConfigVersion,
    expressionMapVersion: store.expressionMapVersion,
    motionMapVersion: store.motionMapVersion,
  }));

  let transitionTimer: ReturnType<typeof setTimeout> | null = null;
  let autoRetreatTimer: ReturnType<typeof setTimeout> | null = null;
  let unlistenPetCommands: UnlistenFn | null = null;
  let scheduledPresentationId: string | null = null;
  // `eventSource` is the live source the controller is currently bound to.
  // It starts as the injected one (legacy path) or whatever the persisted
  // connection mode requires, and gets swapped whenever the user changes
  // the mode and reconnects.
  let eventSource: DesktopEventSource | null =
    initialEventSource ?? createDesktopEventSource(store.gatewayConnection);

  const speechPlayback = new SpeechPlaybackCoordinator(
    new SystemSpeechAdapter(
      undefined,
      undefined,
      () => store.localConfig.ttsSettings,
    ),
    {
      onSegmentStart(presentation, index, _segment, mode) {
        if (store.activePresentation?.id !== presentation.id) return;
        store.setSegment(index, mode === "audio");
      },
      onSegmentFallback(presentation, index) {
        if (store.activePresentation?.id !== presentation.id) return;
        store.setSegment(index, false);
      },
      onPresentationComplete(presentation) {
        if (store.activePresentation?.id !== presentation.id) return;
        store.finishPresentation();
      },
    },
  );

  function clearPetStateTimers() {
    clearTimer(transitionTimer);
    clearTimer(autoRetreatTimer);
    transitionTimer = null;
    autoRetreatTimer = null;
  }

  function startEventSource(
    source: DesktopEventSource | null,
    options?: DesktopEventSourceOptions,
  ) {
    if (!source) return;
    source.start((event) => store.applyDesktopEvent(event), options);
  }

  function stopEventSource() {
    eventSource?.stop();
  }

  function swapEventSource(next: DesktopEventSource) {
    if (eventSource === next) return;
    stopEventSource();
    eventSource = next;
  }

  function connectMockBackend() {
    store.updateGatewayConnection({ mode: "mock" });
    swapEventSource(createDesktopEventSource(store.gatewayConnection));
    startEventSource(eventSource);
  }

  function disconnectMockBackend() {
    stopEventSource();
    store.activePlan = null;
    store.activePresentation = null;
    store.clearPendingAfterEmerge();
  }

  function connectGateway() {
    const settings = store.gatewayConnection;
    if (settings.mode !== "gateway") {
      store.updateGatewayConnection({ mode: "gateway" });
    }
    swapEventSource(createDesktopEventSource(store.gatewayConnection));
    store.setGatewayConnectionError(null);
    startEventSource(eventSource, { connection: store.gatewayConnection });
  }

  function disconnectGateway() {
    stopEventSource();
    store.activePlan = null;
    store.activePresentation = null;
    store.clearPendingAfterEmerge();
  }

  function reconnectGateway() {
    stopEventSource();
    swapEventSource(createDesktopEventSource(store.gatewayConnection));
    store.setGatewayConnectionError(null);
    startEventSource(eventSource, { connection: store.gatewayConnection });
  }

  async function exchangePairingToken(token: string): Promise<PairingCompleteResult> {
    const trimmed = token.trim();
    if (!trimmed) {
      const result: PairingCompleteResult = {
        ok: false,
        error: "Pairing token is empty.",
      };
      if (!result.ok) {
        store.setGatewayPairingState({
          status: "error",
          message: result.error,
        });
      }
      return result;
    }

    store.setGatewayPairingState({ status: "exchanging" });
    const result = await completeGatewayPairing(
      store.gatewayConnection.gatewayWsUrl,
      trimmed,
    );
    if (result.ok) {
      store.updateGatewayConnection({
        nodeToken: result.nodeToken,
        mode: "gateway",
        nodeId: result.nodeId || store.gatewayConnection.nodeId,
        defaultSessionId:
          result.conversationId ?? store.gatewayConnection.defaultSessionId,
      });
      store.setGatewayPairingState({
        status: "success",
        message: `Paired as ${result.nodeId}.`,
      });
    } else {
      store.setGatewayPairingState({
        status: "error",
        message: result.error,
      });
    }
    return result;
  }

  async function pairDeviceFromForm(
    adminBearerToken?: string,
  ): Promise<PairDeviceResult> {
    const settings = store.gatewayConnection;
    store.setGatewayPairingState({ status: "exchanging" });
    const result = await pairDevice({
      gatewayWsUrl: settings.gatewayWsUrl,
      nodeId: settings.nodeId,
      displayName: settings.displayName,
      adminBearerToken,
    });

    if (result.ok) {
      store.updateGatewayConnection({
        mode: "gateway",
        nodeToken: result.nodeToken,
        nodeId: result.nodeId,
        defaultSessionId:
          result.conversationId ?? settings.defaultSessionId,
      });
      const authHint = result.usedAdminBearer
        ? " (via admin token)"
        : " (no admin token required)";
      store.setGatewayPairingState({
        status: "success",
        message: `Paired as ${result.nodeId}${authHint}.`,
      });
    } else {
      store.setGatewayPairingState({
        status: "error",
        message: result.error,
      });
    }
    return result;
  }

  function submitUserMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    store.applyDesktopEvent({
      type: "user.message.submitted",
      source: "local",
      at: new Date().toISOString(),
      sessionId: store.sessionId,
      text: trimmed,
    });
    eventSource?.submitUserMessage(trimmed, store.sessionId);
  }

  function submitMockLlmResult(rawOutput: string) {
    const trimmed = rawOutput.trim();
    if (!trimmed) return;
    eventSource?.submitMockLlmResult(trimmed);
  }

  function scheduleActivePresentation() {
    const presentation = store.activePresentation;
    if (!presentation) {
      scheduledPresentationId = null;
      speechPlayback.stop();
      return;
    }
    if (petRuntimeNeedsEmerge(store.petRuntime.status)) {
      if (
        store.petRuntime.status === "retreating" ||
        store.petRuntime.status === "error"
      ) {
        speechPlayback.stop();
      }
      return;
    }
    if (scheduledPresentationId === presentation.id) return;
    scheduledPresentationId = presentation.id;
    speechPlayback.play(presentation);
  }

  function schedulePetState(status: typeof store.petRuntime.status) {
    clearPetStateTimers();

    // Fallback only: the pet window normally reports `transition_done`
    // when its slide animation settles; without it (browser dev mode)
    // these timers keep the state machine moving.
    if (status === "emerging") {
      transitionTimer = setTimeout(
        () => store.completePetEmerge(),
        desktopWindowDefaults.transitionFallbackMs,
      );
      return;
    }

    if (status === "retreating") {
      transitionTimer = setTimeout(
        () => store.completePetRetreat(),
        desktopWindowDefaults.transitionFallbackMs,
      );
      return;
    }

    if (status === "emerged") {
      autoRetreatTimer = setTimeout(
        () => store.requestPetRetreat(),
        desktopWindowDefaults.autoRetreatMs,
      );
      return;
    }

    if (status === "error") {
      autoRetreatTimer = setTimeout(
        () => store.requestPetRetreat(),
        desktopWindowDefaults.errorRetreatMs,
      );
      return;
    }

    if (status === "chat") {
      autoRetreatTimer = setTimeout(
        () => store.exitPetChat(),
        desktopWindowDefaults.chatIdleTimeoutMs,
      );
    }
  }

  function handlePetCommand(command: PetWindowCommand) {
    switch (command.type) {
      case "request_state":
        void publishRuntimeSnapshot(runtimeSnapshot.value);
        break;
      case "peek":
        store.requestPetPeek();
        break;
      case "emerge":
        store.requestPetEmerge();
        break;
      case "retreat":
        store.requestPetRetreat();
        break;
      case "hide":
        store.requestPetHide();
        break;
      case "enter_chat":
        store.enterPetChat();
        break;
      case "exit_chat":
        store.exitPetChat();
        break;
      case "submit_message":
        submitUserMessage(command.text);
        break;
      case "pointer_activity":
        schedulePetState(store.petRuntime.status);
        break;
      case "transition_done":
        if (command.phase === "emerge") {
          store.completePetEmerge();
        } else {
          store.completePetRetreat();
        }
        break;
    }
  }

  watch(
    () =>
      [
        store.activePresentation?.id ?? null,
        store.petRuntime.status,
      ] as const,
    () => scheduleActivePresentation(),
    { immediate: true },
  );

  watch(
    // lastEventAt re-arms the timers when activity happens without a status
    // change (e.g. a reply playing inside chat keeps the chat session alive).
    () => [store.petRuntime.status, store.petRuntime.lastEventAt] as const,
    ([status]) => schedulePetState(status),
    { immediate: true },
  );

  watch(
    runtimeSnapshot,
    (snapshot) => {
      void publishRuntimeSnapshot(snapshot);
    },
    { deep: true, immediate: true },
  );

  onMounted(async () => {
    unlistenPetCommands = await listenForPetCommands(handlePetCommand);
    if (store.gatewayConnection.mode === "mock") {
      startEventSource(eventSource);
    }
  });

  onBeforeUnmount(() => {
    speechPlayback.dispose();
    clearPetStateTimers();
    unlistenPetCommands?.();
    stopEventSource();
  });

  return {
    connectMockBackend,
    disconnectMockBackend,
    connectGateway,
    disconnectGateway,
    reconnectGateway,
    exchangePairingToken,
    pairDevice: pairDeviceFromForm,
    submitUserMessage,
    submitMockLlmResult,
  };
}
