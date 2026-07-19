<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { GatewayConnectionMode } from "@/domain/gatewayConnection";
import {
  isNodeToken,
  isPairingToken,
  sanitizeGatewayWsUrl,
} from "@/domain/gatewayConnection";
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
});

const adminBearerInput = ref("");
const pairingTokenInput = ref("");
const showToken = ref(false);
const showPairingToken = ref(false);
const showAdminBearer = ref(false);

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
    };
  },
  { deep: true },
);

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
    draft.value.nodeToken !== current.nodeToken
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
  const result = await props.runtime.pairDevice(adminBearerInput.value);
  if (result.ok) {
    adminBearerInput.value = "";
    draft.value.nodeToken = result.nodeToken;
    if (result.conversationId) {
      draft.value.defaultSessionId = result.conversationId;
    }
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
      </div>
    </div>
  </section>
</template>
