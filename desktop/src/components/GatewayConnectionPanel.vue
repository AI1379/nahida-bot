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
  return sanitizedDraftUrl.value ? null : "地址必须以 ws:// 或 wss:// 开头";
});

const nodeTokenWarning = computed<string | null>(() => {
  const trimmed = draft.value.nodeToken.trim();
  if (!trimmed) return null;
  return isNodeToken(trimmed)
    ? null
    : "节点令牌格式应为 nt_xxxxx.yyyyy。";
});

const pairingTokenWarning = computed<string | null>(() => {
  const trimmed = pairingTokenInput.value.trim();
  if (!trimmed) return null;
  return isPairingToken(trimmed)
    ? null
    : "配对令牌格式应为 np_xxxxx.yyyyy。";
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
    return "不绑定账号时，桌面端只能接收事件，不能发送消息。";
  }
  return isValidActorAccountKey(trimmed)
    ? null
    : "格式应为“渠道:user:平台用户 ID”，例如 telegram:user:12345。";
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
    return "正在配对…";
  }
  if (
    draft.value.mode === "gateway" &&
    store.gatewayConnectionStatus === "auth-required"
  ) {
    return "需要身份验证";
  }
  if (
    draft.value.mode === "gateway" &&
    store.gatewayConnectionStatus === "connecting"
  ) {
    return "正在连接…";
  }
  if (store.gatewayConnectionError) {
    return "连接异常";
  }
  if (store.connected) {
    return draft.value.mode === "gateway" ? "Gateway 已连接" : "离线体验已启动";
  }
  return draft.value.mode === "gateway" ? "Gateway 未连接" : "离线体验未启动";
});

const pairingMessage = computed(() => store.gatewayPairing.message ?? "");

const modeOptions: ReadonlyArray<{ value: GatewayConnectionMode; label: string; hint: string }> = [
  {
    value: "mock",
    label: "离线体验",
    hint: "无需运行 Nahida Bot，使用本地模拟数据预览桌宠。",
  },
  {
    value: "gateway",
    label: "Nahida Gateway",
    hint: "连接真实的 Nahida Bot。首次配对后会安全复用凭据。",
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
    aria-label="Gateway 连接设置"
  >
    <header class="panel__header">
      <h2>连接 Nahida Gateway</h2>
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
      <fieldset class="connection-panel__mode">
        <legend>连接方式</legend>
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

      <div v-if="draft.mode === 'gateway'" class="connection-panel__grid">
        <label class="connection-panel__field connection-panel__field--wide">
          <span>Gateway 地址</span>
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
      </div>

      <details
        v-if="draft.mode === 'gateway'"
        class="connection-panel__connection-advanced"
      >
        <summary>高级连接设置</summary>
        <div class="connection-panel__grid">
        <label class="connection-panel__field">
          <span>节点 ID</span>
          <input
            v-model="draft.nodeId"
            type="text"
            spellcheck="false"
            autocomplete="off"
          />
        </label>

        <label class="connection-panel__field">
          <span>设备名称</span>
          <input
            v-model="draft.displayName"
            type="text"
            spellcheck="false"
            autocomplete="off"
          />
        </label>

        <label class="connection-panel__field connection-panel__field--wide">
          <span>默认会话 ID（可选）</span>
          <input
            v-model="draft.defaultSessionId"
            type="text"
            spellcheck="false"
            autocomplete="off"
            placeholder="例如 telegram:private:12345"
          />
        </label>
        </div>
      </details>

      <div class="connection-panel__actions">
        <button
          class="settings-button settings-button--primary"
          type="button"
          :disabled="!canSave"
          @click="saveDraft"
        >
          保存
        </button>
        <button
          class="settings-button settings-button--quiet"
          type="button"
          :disabled="!isDirty"
          @click="revertDraft"
        >
          撤销修改
        </button>
        <button
          class="settings-button settings-button--quiet"
          type="button"
          @click="resetToDefaults"
        >
          恢复默认
        </button>
        <span class="connection-panel__spacer" />
        <template v-if="draft.mode === 'gateway'">
          <button
            v-if="store.connected"
            class="settings-button"
            type="button"
            @click="disconnect"
          >
            断开连接
          </button>
          <button
            v-else
            class="settings-button settings-button--primary"
            type="button"
            :disabled="!canConnect"
            @click="connect"
          >
            连接
          </button>
          <button
            class="settings-button"
            type="button"
            :disabled="!canConnect || !store.connected"
            :title="!store.connected ? '请先连接，再应用新设置' : ''"
            @click="reconnect"
          >
            应用并重连
          </button>
        </template>
        <template v-else>
          <button
            v-if="store.connected"
            class="settings-button"
            type="button"
            @click="disconnect"
          >
            停止体验
          </button>
          <button
            v-else
            class="settings-button settings-button--primary"
            type="button"
            @click="useMock"
          >
            进入离线体验
          </button>
        </template>
      </div>

      <p
        v-if="store.gatewayConnectionError"
        class="connection-panel__error"
      >
        {{ store.gatewayConnectionError }}
      </p>

      <hr v-if="draft.mode === 'gateway'" class="connection-panel__divider" />

      <div v-if="draft.mode === 'gateway'" class="connection-panel__pairing">
        <header class="connection-panel__pairing-header">
          <div>
            <strong>配对这台设备</strong>
            <p class="connection-panel__pairing-hint">
              桌面端会向 Gateway 申请一次性凭据，并换取长期节点令牌。若 Gateway
              启用了管理员验证，请填入 <code>config.yaml</code> 中的
              <code>webapi.auth_token</code>；否则留空即可。
            </p>
          </div>
        </header>

        <label class="connection-panel__field">
          <span>关联账号（推荐）</span>
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
            同一用户可在不同渠道共享长期记忆。桌面会话仍保持独立（<code>{{ derivedConversationId || "desktop:private:&lt;node-id&gt;" }}</code
            >），仅身份关联会跨渠道使用。
          </small>
        </label>

        <label class="connection-panel__field">
          <span>管理员 API 令牌（可选）</span>
          <div class="connection-panel__token-row">
            <input
              v-model="adminBearerInput"
              :type="showAdminBearer ? 'text' : 'password'"
              spellcheck="false"
              autocomplete="off"
              placeholder="仅在 Gateway 启用管理员验证时需要"
            />
            <button
              type="button"
              class="connection-panel__toggle"
              :aria-pressed="showAdminBearer"
              @click="showAdminBearer = !showAdminBearer"
            >
              {{ showAdminBearer ? "隐藏" : "显示" }}
            </button>
          </div>
        </label>

        <div class="connection-panel__actions">
          <button
            class="settings-button settings-button--primary"
            type="button"
            :disabled="!canPairDevice"
            @click="pairDevice"
          >
            配对设备
          </button>
        </div>

        <p
          v-if="store.gatewayPairing.status === 'success'"
          class="connection-panel__pairing-result"
        >
          {{ pairingMessage || "设备配对成功。" }}
        </p>
        <p
          v-else-if="store.gatewayPairing.status === 'error'"
          class="connection-panel__pairing-result connection-panel__pairing-result--error"
        >
          {{ pairingMessage || "设备配对失败。" }}
        </p>

        <details class="connection-panel__pairing-advanced">
          <summary>已有配对令牌？手动换取</summary>
          <p class="connection-panel__pairing-hint">
            如果管理员已通过 <code>/api/nodes/pairing/start</code>
            生成并发送给你 <code>np_…</code> 令牌，可在此使用。
          </p>
          <label class="connection-panel__field">
            <span>配对令牌</span>
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
                {{ showPairingToken ? "隐藏" : "显示" }}
              </button>
            </div>
            <small v-if="pairingTokenWarning" class="connection-panel__hint">
              {{ pairingTokenWarning }}
            </small>
          </label>
          <div class="connection-panel__actions">
            <button
              class="settings-button"
              type="button"
              :disabled="!canExchangeManualPairing"
              @click="exchangeManualPairing"
            >
              换取节点令牌
            </button>
          </div>
        </details>
      </div>

      <hr v-if="draft.mode === 'gateway'" class="connection-panel__divider" />

      <div v-if="draft.mode === 'gateway'" class="connection-panel__token">
        <label class="connection-panel__field">
          <span>节点令牌（配对后自动填写）</span>
          <div class="connection-panel__token-row">
            <input
              v-model="draft.nodeToken"
              :type="showToken ? 'text' : 'password'"
              spellcheck="false"
              autocomplete="off"
              placeholder="nt_xxxxx.yyyyy — 配对完成后会显示在这里"
            />
            <button
              type="button"
              class="connection-panel__toggle"
              :aria-pressed="showToken"
              @click="showToken = !showToken"
            >
              {{ showToken ? "隐藏" : "显示" }}
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
          忘记令牌
        </button>
        <small class="connection-panel__hint connection-panel__hint--muted">
          {{
            hasPlatformCredentialStore
              ? "令牌保存在操作系统凭据库中，不会写入 WebView 存储。"
              : "浏览器预览只在当前会话保存令牌；桌面版会使用操作系统凭据库。"
          }}
        </small>
      </div>

      <div v-if="draft.adminBearerToken" class="connection-panel__token">
        <label class="connection-panel__field">
          <span>管理员 API 令牌（配对时保存）</span>
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
              {{ showAdminBearer ? "隐藏" : "显示" }}
            </button>
          </div>
        </label>
        <button
          type="button"
          class="connection-panel__link"
          @click="draft.adminBearerToken = ''; saveDraft()"
        >
          忘记管理员令牌
        </button>
      </div>

      <div v-if="draft.mode === 'gateway'" class="connection-panel__tts-source">
        <label class="connection-panel__field">
          <span>语音来源</span>
          <select
            :value="draft.ttsSource"
            @change="draft.ttsSource = ($event.target as HTMLSelectElement).value as TtsSourcePreference; saveDraft()"
          >
            <option value="auto">
              自动（优先 Gateway GPT-SoVITS，失败时使用系统语音）
            </option>
            <option value="gateway">
              Gateway（需要管理员 API 令牌）
            </option>
            <option value="system">
              仅使用系统语音
            </option>
          </select>
        </label>
        <small v-if="draft.ttsSource !== 'system' && !draft.adminBearerToken" class="connection-panel__hint">
          Gateway 语音需要管理员 API 令牌，请先使用管理员令牌完成配对。
        </small>
      </div>
    </div>
  </section>
</template>
