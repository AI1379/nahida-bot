<script setup lang="ts">
import { isTauri } from "@tauri-apps/api/core";
import { computed, ref, watch } from "vue";

import type { GatewayConnectionMode, TtsSourcePreference } from "@/domain/gatewayConnection";
import {
  isNodeToken,
  isPairingToken,
  sanitizeGatewayWsUrl,
} from "@/domain/gatewayConnection";
import { isValidActorAccountKey } from "@/services/gatewayPairing";
import type { DesktopRuntimeActions } from "@/runtime/desktopRuntimeController";
import { useDesktopStore } from "@/stores/desktop";

const props = defineProps<{
  runtime: DesktopRuntimeActions;
}>();

const store = useDesktopStore();

const draft = ref({
  mode: store.gatewayConnection.mode,
  gatewayWsUrl: store.gatewayConnection.gatewayWsUrl,
  nodeId: store.gatewayConnection.nodeId,
  displayName: store.gatewayConnection.displayName,
  defaultSessionId: store.gatewayConnection.defaultSessionId,
  nodeToken: store.gatewayConnection.nodeToken,
  adminBearerToken: store.gatewayConnection.adminBearerToken,
  ttsSource: store.gatewayConnection.ttsSource,
});

const adminBearerInput = ref("");
const actorAccountKeyInput = ref("");
const pairingTokenInput = ref("");
const showToken = ref(false);
const showPairingToken = ref(false);
const showAdminBearer = ref(false);
const hasPlatformCredentialStore = isTauri();

watch(
  () => store.gatewayConnection,
  (settings) => {
    draft.value = {
      mode: settings.mode,
      gatewayWsUrl: settings.gatewayWsUrl,
      nodeId: settings.nodeId,
      displayName: settings.displayName,
      defaultSessionId: settings.defaultSessionId,
      nodeToken: settings.nodeToken,
      adminBearerToken: settings.adminBearerToken,
      ttsSource: settings.ttsSource,
    };
  },
  { deep: true },
);

// Seed the admin bearer field from persisted state.
adminBearerInput.value = store.gatewayConnection.adminBearerToken;

const sanitizedDraftUrl = computed(() =>
  sanitizeGatewayWsUrl(draft.value.gatewayWsUrl),
);

const urlWarning = computed<string | null>(() => {
  if (!draft.value.gatewayWsUrl.trim()) return null;
  return sanitizedDraftUrl.value ? null : "URL must be ws:// or wss://";
});

const nodeTokenWarning = computed<string | null>(() => {
  const trimmed = draft.value.nodeToken.trim();
  if (!trimmed) return null;
  return isNodeToken(trimmed)
    ? null
    : "Token should look like nt_xxxxx.yyyyy (issued by the gateway).";
});

const pairingTokenWarning = computed<string | null>(() => {
  const trimmed = pairingTokenInput.value.trim();
  if (!trimmed) return null;
  return isPairingToken(trimmed)
    ? null
    : "Pairing token should look like np_xxxxx.yyyyy.";
});

const isDirty = computed(() => {
  const current = store.gatewayConnection;
  return (
    draft.value.mode !== current.mode ||
    draft.value.gatewayWsUrl !== current.gatewayWsUrl ||
    draft.value.nodeId !== current.nodeId ||
    draft.value.displayName !== current.displayName ||
    draft.value.defaultSessionId !== current.defaultSessionId ||
    draft.value.nodeToken !== current.nodeToken ||
    draft.value.adminBearerToken !== current.adminBearerToken ||
    draft.value.ttsSource !== current.ttsSource
  );
});

const canSave = computed(
  () =>
    isDirty.value &&
    !urlWarning.value &&
    Boolean(draft.value.nodeId.trim()) &&
    Boolean(draft.value.displayName.trim()),
);

const canConnect = computed(
  () =>
    !urlWarning.value &&
    Boolean(draft.value.nodeId.trim()) &&
    draft.value.mode === "gateway" &&
    Boolean(draft.value.nodeToken.trim()),
);

const canPairDevice = computed(() => {
  if (store.gatewayPairing.status === "exchanging") return false;
  if (urlWarning.value) return false;
  if (!draft.value.nodeId.trim()) return false;
  // Admin bearer is optional; pairDevice will surface a precise error if
  // the gateway turns out to require one.
  return draft.value.mode === "gateway";
});

const actorAccountKeyWarning = computed<string | null>(() => {
  const trimmed = actorAccountKeyInput.value.trim();
  if (!trimmed) {
    return "Without an actor binding the desktop can receive events but cannot submit messages.";
  }
  return isValidActorAccountKey(trimmed)
    ? null
    : "Use the format `{channel}:user:{platform_user_id}`, e.g. `telegram:user:12345`.";
});

const derivedConversationId = computed(() =>
  draft.value.nodeId.trim()
    ? `desktop:private:${draft.value.nodeId.trim()}`
    : "",
);

const canExchangeManualPairing = computed(
  () =>
    draft.value.mode === "gateway" &&
    !urlWarning.value &&
    isPairingToken(pairingTokenInput.value.trim()) &&
    store.gatewayPairing.status !== "exchanging",
);

const connectionStatusLabel = computed(() => {
  if (store.gatewayPairing.status === "exchanging") {
    return "Pairing…";
  }
  if (
    draft.value.mode === "gateway" &&
    store.gatewayConnectionStatus === "auth-required"
  ) {
    return "Authentication required";
  }
  if (
    draft.value.mode === "gateway" &&
    store.gatewayConnectionStatus === "connecting"
  ) {
    return "Connecting…";
  }
  if (store.gatewayConnectionError) {
    return "Error";
  }
  if (store.connected) {
    return draft.value.mode === "gateway" ? "Gateway connected" : "Mock connected";
  }
  return draft.value.mode === "gateway" ? "Gateway offline" : "Mock offline";
});

const pairingMessage = computed(() => store.gatewayPairing.message ?? "");

const modeOptions: ReadonlyArray<{ value: GatewayConnectionMode; label: string; hint: string }> = [
  {
    value: "mock",
    label: "Mock backend",
    hint: "In-process fake gateway. Useful for previews without a Nahida Bot running.",
  },
  {
    value: "gateway",
    label: "Nahida Gateway",
    hint: "Connect to a real Nahida Bot Gateway over WebSocket. Pair once and the token is reused.",
  },
];

function saveDraft() {
  if (!canSave.value) return;
  store.updateGatewayConnection({
    mode: draft.value.mode,
    gatewayWsUrl:
      sanitizedDraftUrl.value || store.gatewayConnection.gatewayWsUrl,
    nodeId: draft.value.nodeId.trim(),
    displayName: draft.value.displayName.trim(),
    defaultSessionId: draft.value.defaultSessionId.trim(),
    nodeToken: draft.value.nodeToken.trim(),
    adminBearerToken: draft.value.adminBearerToken.trim(),
    ttsSource: draft.value.ttsSource,
  });
}

function revertDraft() {
  const current = store.gatewayConnection;
  draft.value = {
    mode: current.mode,
    gatewayWsUrl: current.gatewayWsUrl,
    nodeId: current.nodeId,
    displayName: current.displayName,
    defaultSessionId: current.defaultSessionId,
    nodeToken: current.nodeToken,
    adminBearerToken: current.adminBearerToken,
    ttsSource: current.ttsSource,
  };
}

function connect() {
  saveDraft();
  props.runtime.connectGateway();
}

function reconnect() {
  saveDraft();
  props.runtime.reconnectGateway();
}

function disconnect() {
  props.runtime.disconnectGateway();
}

function useMock() {
  saveDraft();
  props.runtime.connectMockBackend();
}

async function pairDevice() {
  if (!canPairDevice.value) return;
  // Persist the URL/node/displayName the user just typed so pairDevice reads
  // the resolved gateway URL.
  saveDraft();
  const result = await props.runtime.pairDevice(
    adminBearerInput.value,
    actorAccountKeyInput.value,
  );
  if (result.ok) {
    // Keep the admin bearer so Desktop can reuse it for /api/speech/jobs.
    draft.value.adminBearerToken = adminBearerInput.value;
    draft.value.nodeToken = result.nodeToken;
    if (result.conversationId) {
      draft.value.defaultSessionId = result.conversationId;
    }
    saveDraft();
  }
}

async function exchangeManualPairing() {
  if (!canExchangeManualPairing.value) return;
  saveDraft();
  const result = await props.runtime.exchangePairingToken(
    pairingTokenInput.value,
  );
  if (result.ok) {
    pairingTokenInput.value = "";
    draft.value.nodeToken = result.nodeToken;
    if (result.conversationId) {
      draft.value.defaultSessionId = result.conversationId;
    }
  }
}

function clearNodeToken() {
  draft.value.nodeToken = "";
  store.clearGatewayNodeToken();
}

function resetToDefaults() {
  store.resetGatewayConnection();
  store.setGatewayPairingState({ status: "idle" });
  adminBearerInput.value = "";
  actorAccountKeyInput.value = "";
  pairingTokenInput.value = "";
}
</script>

<template>
  <section
    class="panel connection-panel"
    aria-label="Gateway connection settings"
  >
    <header class="panel__header">
      <h2>Gateway Connection</h2>
      <span
        class="connection-panel__status"
        :data-state="
          store.connected
            ? 'connected'
            : store.gatewayConnectionError
              ? 'error'
              : 'offline'
        "
      >
        {{ connectionStatusLabel }}
      </span>
    </header>

    <div class="connection-panel__body">
      <fieldset class="connection-panel__mode" legend="Mode">
        <legend>Connection mode</legend>
        <label
          v-for="option in modeOptions"
          :key="option.value"
          class="connection-panel__mode-option"
          :class="{ 'is-active': draft.mode === option.value }"
        >
          <input
            v-model="draft.mode"
            type="radio"
            :value="option.value"
            name="gateway-connection-mode"
          />
          <span>
            <strong>{{ option.label }}</strong>
            <em>{{ option.hint }}</em>
          </span>
        </label>
      </fieldset>

      <div class="connection-panel__grid">
        <label class="connection-panel__field">
          <span>Gateway WebSocket URL</span>
          <input
            v-model="draft.gatewayWsUrl"
            type="text"
            inputmode="url"
            spellcheck="false"
            autocomplete="off"
            placeholder="ws://127.0.0.1:6185/api/nodes/ws"
            :aria-invalid="Boolean(urlWarning)"
          />
          <small v-if="urlWarning" class="connection-panel__hint">{{ urlWarning }}</small>
        </label>

        <label class="connection-panel__field">
          <span>Node ID</span>
          <input
            v-model="draft.nodeId"
            type="text"
            spellcheck="false"
            autocomplete="off"
          />
        </label>

        <label class="connection-panel__field">
          <span>Display name</span>
          <input
            v-model="draft.displayName"
            type="text"
            spellcheck="false"
            autocomplete="off"
          />
        </label>

        <label class="connection-panel__field">
          <span>Default session ID (optional)</span>
          <input
            v-model="draft.defaultSessionId"
            type="text"
            spellcheck="false"
            autocomplete="off"
            placeholder="e.g. telegram:private:12345"
          />
        </label>
      </div>

      <div class="connection-panel__actions">
        <button
          type="button"
          :disabled="!canSave"
          @click="saveDraft"
        >
          Save
        </button>
        <button
          type="button"
          :disabled="!isDirty"
          @click="revertDraft"
        >
          Revert
        </button>
        <button type="button" @click="resetToDefaults">Reset</button>
        <span class="connection-panel__spacer" />
        <template v-if="draft.mode === 'gateway'">
          <button
            v-if="store.connected"
            type="button"
            @click="disconnect"
          >
            Disconnect
          </button>
          <button
            v-else
            type="button"
            :disabled="!canConnect"
            @click="connect"
          >
            Connect
          </button>
          <button
            type="button"
            :disabled="!canConnect || !store.connected"
            :title="!store.connected ? 'Connect first to apply new settings' : ''"
            @click="reconnect"
          >
            Apply &amp; Reconnect
          </button>
        </template>
        <template v-else>
          <button
            v-if="store.connected"
            type="button"
            @click="disconnect"
          >
            Disconnect
          </button>
          <button v-else type="button" @click="useMock">Start Mock</button>
        </template>
      </div>

      <p
        v-if="store.gatewayConnectionError"
        class="connection-panel__error"
      >
        {{ store.gatewayConnectionError }}
      </p>

      <hr class="connection-panel__divider" />

      <div class="connection-panel__pairing">
        <header class="connection-panel__pairing-header">
          <div>
            <strong>Pair this device</strong>
            <p class="connection-panel__pairing-hint">
              Desktop will ask the gateway to mint a one-shot pairing token
              and immediately exchange it for a long-lived node token. If the
              gateway has admin auth enabled, paste the
              <code>webapi.auth_token</code> from its
              <code>config.yaml</code>; otherwise leave the field blank.
            </p>
          </div>
        </header>

        <label class="connection-panel__field">
          <span>Actor account key (recommended)</span>
          <input
            v-model="actorAccountKeyInput"
            type="text"
            spellcheck="false"
            autocomplete="off"
            placeholder="telegram:user:12345"
          />
          <small
            class="connection-panel__hint"
            :class="{ 'connection-panel__hint--warn': !actorAccountKeyInput.trim() }"
          >
            {{ actorAccountKeyWarning }}
          </small>
          <small class="connection-panel__hint connection-panel__hint--muted">
            Same person across channels shares long-term memory. The desktop
            session stays independent (<code>{{ derivedConversationId || "desktop:private:&lt;node-id&gt;" }}</code
            >); only the identity binding crosses channels.
          </small>
        </label>

        <label class="connection-panel__field">
          <span>Admin API token (optional)</span>
          <div class="connection-panel__token-row">
            <input
              v-model="adminBearerInput"
              :type="showAdminBearer ? 'text' : 'password'"
              spellcheck="false"
              autocomplete="off"
              placeholder="Only required when the gateway requires admin auth"
            />
            <button
              type="button"
              class="connection-panel__toggle"
              :aria-pressed="showAdminBearer"
              @click="showAdminBearer = !showAdminBearer"
            >
              {{ showAdminBearer ? "Hide" : "Show" }}
            </button>
          </div>
        </label>

        <div class="connection-panel__actions">
          <button
            type="button"
            :disabled="!canPairDevice"
            @click="pairDevice"
          >
            Pair this device
          </button>
        </div>

        <p
          v-if="store.gatewayPairing.status === 'success'"
          class="connection-panel__pairing-result"
        >
          {{ pairingMessage || "Pairing succeeded." }}
        </p>
        <p
          v-else-if="store.gatewayPairing.status === 'error'"
          class="connection-panel__pairing-result connection-panel__pairing-result--error"
        >
          {{ pairingMessage || "Pairing failed." }}
        </p>

        <details class="connection-panel__pairing-advanced">
          <summary>Already have a pairing token? Exchange it manually</summary>
          <p class="connection-panel__pairing-hint">
            Use this if an admin already ran
            <code>/api/nodes/pairing/start</code> and handed you the
            <code>np_…</code> token out-of-band.
          </p>
          <label class="connection-panel__field">
            <span>Pairing token</span>
            <div class="connection-panel__token-row">
              <input
                v-model="pairingTokenInput"
                :type="showPairingToken ? 'text' : 'password'"
                spellcheck="false"
                autocomplete="off"
                placeholder="np_xxxxx.yyyyy"
              />
              <button
                type="button"
                class="connection-panel__toggle"
                :aria-pressed="showPairingToken"
                @click="showPairingToken = !showPairingToken"
              >
                {{ showPairingToken ? "Hide" : "Show" }}
              </button>
            </div>
            <small v-if="pairingTokenWarning" class="connection-panel__hint">
              {{ pairingTokenWarning }}
            </small>
          </label>
          <div class="connection-panel__actions">
            <button
              type="button"
              :disabled="!canExchangeManualPairing"
              @click="exchangeManualPairing"
            >
              Exchange pairing token
            </button>
          </div>
        </details>
      </div>

      <hr class="connection-panel__divider" />

      <div class="connection-panel__token">
        <label class="connection-panel__field">
          <span>Node token (auto-filled after pairing)</span>
          <div class="connection-panel__token-row">
            <input
              v-model="draft.nodeToken"
              :type="showToken ? 'text' : 'password'"
              spellcheck="false"
              autocomplete="off"
              placeholder="nt_xxxxx.yyyyy — appears here after pairing"
            />
            <button
              type="button"
              class="connection-panel__toggle"
              :aria-pressed="showToken"
              @click="showToken = !showToken"
            >
              {{ showToken ? "Hide" : "Show" }}
            </button>
          </div>
          <small v-if="nodeTokenWarning" class="connection-panel__hint">
            {{ nodeTokenWarning }}
          </small>
        </label>
        <button
          v-if="draft.nodeToken"
          type="button"
          class="connection-panel__link"
          @click="clearNodeToken"
        >
          Forget token
        </button>
        <small class="connection-panel__hint connection-panel__hint--muted">
          {{
            hasPlatformCredentialStore
              ? "Saved in the operating system credential store; never written to WebView storage."
              : "Browser preview keeps tokens for this session only. Desktop builds use the operating system credential store."
          }}
        </small>
      </div>

      <div v-if="draft.adminBearerToken" class="connection-panel__token">
        <label class="connection-panel__field">
          <span>Admin API token (saved from pairing)</span>
          <div class="connection-panel__token-row">
            <input
              v-model="draft.adminBearerToken"
              :type="showAdminBearer ? 'text' : 'password'"
              spellcheck="false"
              autocomplete="off"
              readonly
            />
            <button
              type="button"
              class="connection-panel__toggle"
              :aria-pressed="showAdminBearer"
              @click="showAdminBearer = !showAdminBearer"
            >
              {{ showAdminBearer ? "Hide" : "Show" }}
            </button>
          </div>
        </label>
        <button
          type="button"
          class="connection-panel__link"
          @click="draft.adminBearerToken = ''; saveDraft()"
        >
          Forget admin token
        </button>
      </div>

      <div v-if="draft.mode === 'gateway'" class="connection-panel__tts-source">
        <label class="connection-panel__field">
          <span>TTS source</span>
          <select
            :value="draft.ttsSource"
            @change="draft.ttsSource = ($event.target as HTMLSelectElement).value as TtsSourcePreference; saveDraft()"
          >
            <option value="auto">
              Auto (gateway GPT-SoVITS, fallback to system)
            </option>
            <option value="gateway">
              Gateway (requires admin API token)
            </option>
            <option value="system">
              System Web Speech only
            </option>
          </select>
        </label>
        <small v-if="draft.ttsSource !== 'system' && !draft.adminBearerToken" class="connection-panel__hint">
          Gateway TTS requires the admin API token above; pair with an admin token first.
        </small>
      </div>
    </div>
  </section>
</template>
