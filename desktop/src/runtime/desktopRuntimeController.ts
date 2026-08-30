import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  watch,
} from "vue";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { desktopWindowDefaults } from "@/config/desktopRuntimeDefaults";
import type {
  DesktopRuntimeSnapshot,
  PetWindowCommand,
} from "@/domain/desktopWindowProtocol";
import { petRuntimeNeedsEmerge } from "@/domain/petRuntimeMachine";
import type { DesktopEvent } from "@/domain/runtime";
import {
  listenForPetCommands,
  publishLipSyncEnergy,
  publishRuntimeSnapshot,
} from "@/services/desktopWindowBridge";
import { SpeechPlaybackCoordinator } from "@/services/speechPlaybackCoordinator";
import { SystemSpeechAdapter } from "@/services/systemSpeechAdapter";
import { GatewayAudioAdapter } from "@/services/gatewayAudioAdapter";
import { PomodoroService } from "@/services/pomodoroService";
import type {
  PomodoroPhase,
  PomodoroReminderKind,
} from "@/services/pomodoroService";
import { fetchGeneratedPomodoroReminder } from "@/services/pomodoroTextService";
import {
  applyPomodoroCapability,
  POMODORO_CONTROL_CAPABILITY,
} from "@/services/pomodoroCapability";
import type { AudioPlaybackAdapter, AudioPlaybackRequest } from "@/services/audioPlaybackAdapter";
import { isAudioPlaybackAborted } from "@/services/audioPlaybackAdapter";
import {
  completeGatewayPairing,
  pairDevice,
  type PairDeviceResult,
  type PairingCompleteResult,
} from "@/services/gatewayPairing";
import { addPairedActorToRemoteControlPolicy } from "@/services/remoteControlPolicy";
import {
  createDesktopEventSource,
  type DesktopEventSource,
  type DesktopEventSourceOptions,
  type MessageSubmitResult,
} from "@/runtime/desktopEventSource";
import type { useDesktopStore } from "@/stores/desktop";
import { isGatewayConnectionConfigured } from "@/domain/gatewayConnection";
import { gatewayWsUrlToHttpBase } from "@/domain/gatewayConnection";

type DesktopStore = ReturnType<typeof useDesktopStore>;

export interface DesktopRuntimeActions {
  connectMockBackend(): void;
  disconnectMockBackend(): void;
  connectGateway(): void;
  disconnectGateway(): void;
  reconnectGateway(): void;
  exchangePairingToken(token: string): Promise<PairingCompleteResult>;
  pairDevice(
    adminBearerToken?: string,
    actorAccountKey?: string,
  ): Promise<PairDeviceResult>;
  submitUserMessage(text: string): Promise<MessageSubmitResult>;
  submitMockLlmResult(rawOutput: string): void;
  /** Start/restart the local pomodoro timer. */
  startPomodoro(): void;
  stopPomodoro(): void;
  togglePomodoro(): void;
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
    turns: store.turns,
    activeMotionFeedbackPlaybackId: store.activeMotionFeedbackPlaybackId,
    pomodoro: store.pomodoroState,
  }));

  let transitionTimer: ReturnType<typeof setTimeout> | null = null;
  let autoRetreatTimer: ReturnType<typeof setTimeout> | null = null;
  let unlistenPetCommands: UnlistenFn | null = null;
  // `eventSource` is the live source the controller is currently bound to.
  // It starts as the injected one (legacy path) or whatever the persisted
  // connection mode requires, and gets swapped whenever the user changes
  // the mode and reconnects.
  let eventSource: DesktopEventSource | null =
    initialEventSource ?? createDesktopEventSource(store.gatewayConnection);

  const systemSpeechAdapter = new SystemSpeechAdapter(
    undefined,
    undefined,
    () => store.localConfig.ttsSettings,
  );

  const gatewayTtsAdapter = new GatewayAudioAdapter(
    () => store.gatewayConnection.adminBearerToken,
    () => store.localConfig.ttsSettings,
    () => store.gatewayConnection.gatewayWsUrl,
    (energy) => void publishLipSyncEnergy(energy),
  );

  const speechPlaybackAdapter: AudioPlaybackAdapter = {
    isAvailable(): boolean {
      const source = store.gatewayConnection.ttsSource;
      if (source === "system") return systemSpeechAdapter.isAvailable();
      return gatewayTtsAdapter.isAvailable() || systemSpeechAdapter.isAvailable();
    },
    async play(request: AudioPlaybackRequest, signal: AbortSignal): Promise<void> {
      const handle = await this.fetch(request, signal);
      await handle.play(signal);
    },
    async fetch(request: AudioPlaybackRequest, signal: AbortSignal) {
      const source = store.gatewayConnection.ttsSource;
      if (source === "system") {
        return systemSpeechAdapter.fetch(request, signal);
      }
      if (source === "gateway") {
        return gatewayTtsAdapter.fetch(request, signal);
      }
      if (gatewayTtsAdapter.isAvailable()) {
        try {
          return await gatewayTtsAdapter.fetch(request, signal);
        } catch (error) {
          if (isAudioPlaybackAborted(error)) throw error;
          return systemSpeechAdapter.fetch(request, signal);
        }
      }
      return systemSpeechAdapter.fetch(request, signal);
    },
    stop(): void {
      systemSpeechAdapter.stop();
      gatewayTtsAdapter.stop();
    },
  };

  const speechPlayback = new SpeechPlaybackCoordinator(
    speechPlaybackAdapter,
    {
      onPresentationStart(presentation) {
        store.startPresentation(presentation);
      },
      async onSegmentStart(presentation, index, _segment, mode, durationMs) {
        if (store.activePresentation?.id !== presentation.id) return;
        store.setSegment(index, mode === "audio", durationMs);
        store.markPresentationTurn(presentation.id, "playing");
        // Commit the segment and its real audio duration to the pet window
        // before the audio element begins consuming the segment.
        await publishRuntimeSnapshot(runtimeSnapshot.value).catch(
          () => undefined,
        );
        await nextTick();
      },
      onSegmentFallback(presentation, index) {
        if (store.activePresentation?.id !== presentation.id) return;
        store.setSegment(index, false);
      },
      onPresentationComplete(presentation) {
        if (store.activePresentation?.id !== presentation.id) return;
        store.markPresentationTurn(presentation.id, "completed");
        store.finishPresentation();
      },
      onPresentationInterrupted(presentation) {
        store.markPresentationTurn(presentation.id, "completed");
        if (store.activePresentation?.id === presentation.id) {
          store.finishPresentation();
        }
      },
      onPresentationError(presentation, error) {
        store.markPresentationTurn(
          presentation.id,
          "failed",
          `Playback failed: ${error instanceof Error ? error.message : String(error)}`,
        );
        if (store.activePresentation?.id === presentation.id) {
          store.finishPresentation();
        }
      },
    },
  );

  function clearPetStateTimers() {
    clearTimer(transitionTimer);
    clearTimer(autoRetreatTimer);
    transitionTimer = null;
    autoRetreatTimer = null;
  }

  async function openMainWindow() {
    try {
      const appWindow = getCurrentWindow();
      await appWindow.unminimize();
      await appWindow.show();
      await appWindow.setFocus();
    } catch {
      // Browser dev mode has no native windows to manage.
    }
  }

  function startEventSource(
    source: DesktopEventSource | null,
    options?: DesktopEventSourceOptions,
  ) {
    if (!source) return;
    source.start(handleDesktopEvent, options);
  }

  function handleDesktopEvent(event: DesktopEvent): unknown {
    if (
      event.type === "capability.invoked" &&
      event.capability === POMODORO_CONTROL_CAPABILITY
    ) {
      return applyPomodoroCapability(
        {
          service: pomodoroService,
          getSettings: () => store.localConfig.pomodoro,
          updateSettings: (settings) => store.updatePomodoroSettings(settings),
        },
        event.arguments,
      );
    }
    return store.applyDesktopEvent(event);
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
    store.setGatewayConnectionStatus("disconnected");
    store.setGatewayConnectionError(null);
    swapEventSource(createDesktopEventSource(store.gatewayConnection));
    startEventSource(eventSource);
  }

  function disconnectMockBackend() {
    stopEventSource();
    speechPlayback.stop();
    store.clearPresentations();
    store.clearPendingAfterEmerge();
  }

  function connectGateway() {
    const settings = store.gatewayConnection;
    if (settings.mode !== "gateway") {
      store.updateGatewayConnection({ mode: "gateway" });
    }
    swapEventSource(createDesktopEventSource(store.gatewayConnection));
    store.setGatewayConnectionError(null);
    store.setGatewayConnectionStatus("connecting");
    startEventSource(eventSource, { connection: store.gatewayConnection });
  }

  function disconnectGateway() {
    stopEventSource();
    store.setGatewayConnectionStatus("disconnected");
    speechPlayback.stop();
    store.clearPresentations();
    store.clearPendingAfterEmerge();
  }

  function reconnectGateway() {
    stopEventSource();
    swapEventSource(createDesktopEventSource(store.gatewayConnection));
    store.setGatewayConnectionError(null);
    store.setGatewayConnectionStatus("connecting");
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
      const whitelistNote = result.actorAccountKey
        ? await describeWhitelistUpdate(result.actorAccountKey)
        : "";
      store.setGatewayPairingState({
        status: "success",
        message: `Paired as ${result.nodeId}${whitelistNote}.`,
      });
    } else {
      store.setGatewayPairingState({
        status: "error",
        message: result.error,
      });
    }
    return result;
  }

  /**
   * Best-effort: append the paired actor to the local remote-control
   * whitelist so remote exec works once a mode is enabled. Failures become
   * a message suffix, never a pairing failure.
   */
  async function describeWhitelistUpdate(actorAccountKey: string): Promise<string> {
    const update = await addPairedActorToRemoteControlPolicy(actorAccountKey);
    if (update.added) return "; actor added to the remote-control whitelist";
    if (update.error) {
      return `; remote-control whitelist not updated (${update.error})`;
    }
    return "";
  }

  async function pairDeviceFromForm(
    adminBearerToken?: string,
    actorAccountKey?: string,
  ): Promise<PairDeviceResult> {
    const settings = store.gatewayConnection;
    store.setGatewayPairingState({ status: "exchanging" });
    const result = await pairDevice({
      gatewayWsUrl: settings.gatewayWsUrl,
      nodeId: settings.nodeId,
      displayName: settings.displayName,
      actorAccountKey,
      // Use any explicit session the user typed; otherwise let pairDevice
      // auto-derive `desktop:private:{nodeId}` so the node gets an
      // independent session lane.
      conversationId: settings.defaultSessionId || undefined,
      adminBearerToken,
    });

    if (result.ok) {
      store.updateGatewayConnection({
        mode: "gateway",
        nodeToken: result.nodeToken,
        nodeId: result.nodeId,
        defaultSessionId:
          result.conversationId ?? settings.defaultSessionId,
        // Keep the admin bearer for ongoing REST calls (e.g. /api/speech/jobs).
        // On no-auth gateways adminBearerInput will be empty; that is fine.
        adminBearerToken: adminBearerToken?.trim() ?? "",
      });
      const authHint = result.usedAdminBearer
        ? " (via admin token)"
        : " (no admin token required)";
      const boundActor = result.actorAccountKey ?? actorAccountKey?.trim();
      const actorHint = boundActor
        ? ` bound to ${boundActor}`
        : " without an actor binding (chat submit will be rejected)";
      const whitelistNote = boundActor
        ? await describeWhitelistUpdate(boundActor)
        : "";
      store.setGatewayPairingState({
        status: "success",
        message: `Paired as ${result.nodeId}${authHint}${actorHint}${whitelistNote}.`,
      });
    } else {
      store.setGatewayPairingState({
        status: "error",
        message: result.error,
      });
    }
    return result;
  }

  async function submitUserMessage(text: string): Promise<MessageSubmitResult> {
    const trimmed = text.trim();
    if (!trimmed) {
      return { ok: false, error: "Message is empty.", retryable: false };
    }
    const at = new Date().toISOString();
    const requestedSessionId =
      store.gatewayConnection.mode === "gateway"
        ? store.gatewayConnection.defaultSessionId || store.sessionId
        : store.sessionId;
    store.sessionId = requestedSessionId;
    const turn = store.beginUserTurn(trimmed, requestedSessionId, at);
    store.applyDesktopEvent({
      type: "user.message.submitted",
      source: "local",
      at,
      sessionId: requestedSessionId,
      text: trimmed,
    });
    if (!eventSource) {
      const result: MessageSubmitResult = {
        ok: false,
        error: "Desktop event source is unavailable.",
        retryable: true,
      };
      store.markTurnFailed(turn.id, result.error);
      return result;
    }
    let result: MessageSubmitResult;
    try {
      result = await eventSource.submitUserMessage(trimmed, requestedSessionId);
    } catch (error) {
      result = {
        ok: false,
        error: `Failed to submit message: ${
          error instanceof Error ? error.message : String(error)
        }`,
        retryable: true,
      };
    }
    if (result.ok) {
      if (result.sessionId !== turn.sessionId) {
        store.updateTurn(turn.id, { sessionId: result.sessionId });
        store.sessionId = result.sessionId;
      }
      store.markTurnAccepted(turn.id);
    } else {
      store.markTurnFailed(turn.id, result.error);
    }
    return result;
  }

  function submitMockLlmResult(rawOutput: string) {
    const trimmed = rawOutput.trim();
    if (!trimmed) return;
    eventSource?.submitMockLlmResult(trimmed);
  }

  const pomodoroService = new PomodoroService({
    getSettings: () => store.localConfig.pomodoro,
    onTick: (event) => store.applyDesktopEvent(event),
    onStateChange: (state) => {
      store.setPomodoroState(state);
      schedulePomodoroPrefetch(state);
    },
    getReminderText: (kind) =>
      store.localConfig.pomodoro.dynamicText
        ? pomodoroGeneratedTexts.get(kind) ?? null
        : null,
  });

  // Dynamic reminder lines are generated + pre-synthesized on the Gateway
  // during the current phase's runway so the next transition plays instantly.
  const pomodoroGeneratedTexts = new Map<PomodoroReminderKind, string>();
  const pomodoroRecentTexts: string[] = [];
  let pomodoroPrefetchController: AbortController | null = null;

  function schedulePomodoroPrefetch(state: {
    phase: PomodoroPhase;
    round: number;
    totalRounds: number;
  }): void {
    if (state.phase === "working") {
      void prefetchPomodoroReminders(["break-start"]);
    } else if (state.phase === "breaking") {
      const kinds: PomodoroReminderKind[] =
        state.round >= state.totalRounds
          ? ["rounds-done"]
          : ["break-end", "work-start"];
      void prefetchPomodoroReminders(kinds);
    }
  }

  async function prefetchPomodoroReminders(
    kinds: PomodoroReminderKind[],
  ): Promise<void> {
    if (!store.localConfig.pomodoro.dynamicText) return;
    const bearer = store.gatewayConnection.adminBearerToken;
    const httpBase = gatewayWsUrlToHttpBase(
      store.gatewayConnection.gatewayWsUrl,
    );
    if (!bearer || !httpBase) return;

    pomodoroPrefetchController?.abort();
    const controller = new AbortController();
    pomodoroPrefetchController = controller;
    const synthesize = store.gatewayConnection.ttsSource !== "system";

    await Promise.all(
      kinds.map(async (kind) => {
        try {
          const reminder = await fetchGeneratedPomodoroReminder({
            httpBase,
            bearer,
            kind,
            avoid: [...pomodoroRecentTexts],
            synthesize,
            model: store.localConfig.pomodoro.dynamicTextModel,
            signal: controller.signal,
          });
          if (controller.signal.aborted) return;
          pomodoroGeneratedTexts.set(kind, reminder.text);
          pomodoroRecentTexts.push(reminder.text);
          if (pomodoroRecentTexts.length > 12) {
            pomodoroRecentTexts.shift();
          }
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") {
            return;
          }
          // Silent fallback: the static settings text is used instead.
        }
      }),
    );
  }

  function startPomodoro() {
    pomodoroService.start();
  }

  function stopPomodoro() {
    pomodoroService.stop();
  }

  function togglePomodoro() {
    pomodoroService.toggle();
  }

  function schedulePendingPresentations() {
    if (!store.pendingPresentations.length) return;
    if (petRuntimeNeedsEmerge(store.petRuntime.status)) {
      if (
        store.petRuntime.status === "retreating" ||
        store.petRuntime.status === "error"
      ) {
        speechPlayback.stop();
      }
      return;
    }
    for (const presentation of store.takePendingPresentations()) {
      speechPlayback.play(presentation);
    }
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
        store.localConfig.petTriggers.autoRetreatMs,
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
        store.localConfig.petTriggers.chatIdleTimeoutMs,
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
      case "toggle_pomodoro":
        togglePomodoro();
        break;
      case "submit_message":
        submitUserMessage(command.text);
        break;
      case "pointer_activity":
        schedulePetState(store.petRuntime.status);
        break;
      case "open_main_window":
        void openMainWindow();
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
        store.pendingPresentations
          .map((presentation) => presentation.id)
          .join("|"),
        store.petRuntime.status,
      ] as const,
    () => schedulePendingPresentations(),
    { immediate: true },
  );

  watch(
    // lastEventAt re-arms the timers when activity happens without a status
    // change (e.g. a reply playing inside chat keeps the chat session alive);
    // petTriggers re-arms them when the user edits the trigger settings.
    () =>
      [
        store.petRuntime.status,
        store.petRuntime.lastEventAt,
        store.localConfig.petTriggers,
      ] as const,
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
    } else if (isGatewayConnectionConfigured(store.gatewayConnection)) {
      store.setGatewayConnectionStatus("connecting");
      startEventSource(eventSource, { connection: store.gatewayConnection });
    } else {
      store.setGatewayConnectionStatus("auth-required");
      store.setGatewayConnectionError(
        "Gateway authentication is required. Pair this desktop or provide a valid node token.",
      );
    }
  });

  onBeforeUnmount(() => {
    speechPlayback.dispose();
    gatewayTtsAdapter.dispose();
    pomodoroService.dispose();
    pomodoroPrefetchController?.abort();
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
    startPomodoro,
    stopPomodoro,
    togglePomodoro,
  };
}
