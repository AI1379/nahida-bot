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
  createDefaultDesktopEventSource,
  type DesktopEventSource,
} from "@/runtime/desktopEventSource";
import type { useDesktopStore } from "@/stores/desktop";

type DesktopStore = ReturnType<typeof useDesktopStore>;

export interface DesktopRuntimeActions {
  connectMockBackend(): void;
  disconnectMockBackend(): void;
  submitUserMessage(text: string): void;
  submitMockLlmResult(rawOutput: string): void;
}

function clearTimer(timer: ReturnType<typeof setTimeout> | null) {
  if (timer !== null) clearTimeout(timer);
}

export function useDesktopRuntimeController(
  store: DesktopStore,
  eventSource: DesktopEventSource = createDefaultDesktopEventSource(),
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

  function connectMockBackend() {
    eventSource.start((event) => store.applyDesktopEvent(event));
  }

  function disconnectMockBackend() {
    eventSource.stop();
    store.activePlan = null;
    store.activePresentation = null;
    store.clearPendingAfterEmerge();
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
    eventSource.submitUserMessage(trimmed, store.sessionId);
  }

  function submitMockLlmResult(rawOutput: string) {
    const trimmed = rawOutput.trim();
    if (!trimmed) return;
    eventSource.submitMockLlmResult(trimmed);
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
    connectMockBackend();
  });

  onBeforeUnmount(() => {
    speechPlayback.dispose();
    clearPetStateTimers();
    unlistenPetCommands?.();
    disconnectMockBackend();
  });

  return {
    connectMockBackend,
    disconnectMockBackend,
    submitUserMessage,
    submitMockLlmResult,
  };
}
