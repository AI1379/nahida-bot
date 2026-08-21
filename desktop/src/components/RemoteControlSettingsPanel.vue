<script setup lang="ts">
import { invoke, isTauri } from "@tauri-apps/api/core";
import { computed, onMounted, ref } from "vue";

import {
  defaultRemoteControlPolicy,
  parseRemoteControlPolicy,
  type RemoteControlMode,
  type RemoteControlPolicy,
} from "@/services/remoteControlPolicy";

const available = isTauri();
const source = ref(JSON.stringify(defaultRemoteControlPolicy, null, 2));
const loading = ref(false);
const saving = ref(false);
const message = ref(available ? "" : "Policy editing is available in the Tauri desktop app.");
const failed = ref(false);
const loadedMode = ref<RemoteControlMode>("disabled");
const loadedAllowScreenCapture = ref(false);
const loadedAllowInput = ref(false);
const editedMode = computed<RemoteControlMode | null>(() => {
  try {
    return parseRemoteControlPolicy(source.value).mode;
  } catch {
    return null;
  }
});
const isFullAccess = computed(() => editedMode.value === "full_access");
const editedComputerUse = computed(() => {
  try {
    return parseRemoteControlPolicy(source.value).computerUse;
  } catch {
    return null;
  }
});

onMounted(() => {
  if (available) void loadPolicy();
});

async function loadPolicy() {
  loading.value = true;
  failed.value = false;
  try {
    const policy = await invoke<RemoteControlPolicy>("remote_control_policy_read");
    source.value = JSON.stringify(policy, null, 2);
    loadedMode.value = policy.mode;
    loadedAllowScreenCapture.value = policy.computerUse.allowScreenCapture;
    loadedAllowInput.value = policy.computerUse.allowInput;
    message.value = "Policy loaded from Rust-owned local storage.";
  } catch (error) {
    failed.value = true;
    message.value = String(error);
  } finally {
    loading.value = false;
  }
}

async function savePolicy() {
  failed.value = false;
  let policy: RemoteControlPolicy;
  try {
    policy = parseRemoteControlPolicy(source.value);
  } catch (error) {
    failed.value = true;
    message.value = error instanceof Error ? error.message : String(error);
    return;
  }
  if (
    policy.mode === "full_access" &&
    loadedMode.value !== "full_access" &&
    !window.confirm(
      "Enable full access? Authorized remote actors will be able to run arbitrary programs and read arbitrary text files on this computer.",
    )
  ) {
    message.value = "Full access was not enabled.";
    return;
  }
  if (
    policy.computerUse.allowScreenCapture &&
    !loadedAllowScreenCapture.value &&
    !window.confirm(
      "Enable screen capture? Authorized remote actors will be able to capture all visible pixels across the virtual desktop, which may include sensitive information.",
    )
  ) {
    message.value = "Screen capture was not enabled.";
    return;
  }
  if (
    policy.computerUse.allowInput &&
    !loadedAllowInput.value &&
    !window.confirm(
      "Enable computer input? Authorized remote actors will be able to move the pointer, click, scroll, type text, and press keys on this computer.",
    )
  ) {
    message.value = "Computer input was not enabled.";
    return;
  }
  saving.value = true;
  try {
    await invoke("remote_control_policy_save", { policy });
    source.value = JSON.stringify(policy, null, 2);
    loadedMode.value = policy.mode;
    loadedAllowScreenCapture.value = policy.computerUse.allowScreenCapture;
    loadedAllowInput.value = policy.computerUse.allowInput;
    message.value = "Local pre-authorization policy saved.";
  } catch (error) {
    failed.value = true;
    message.value = String(error);
  } finally {
    saving.value = false;
  }
}

function setMode(event: Event) {
  const mode = (event.target as HTMLSelectElement).value as RemoteControlMode;
  try {
    const policy = parseRemoteControlPolicy(source.value);
    source.value = JSON.stringify({ ...policy, mode }, null, 2);
    failed.value = false;
    message.value = mode === "full_access"
      ? "Full access is staged. Saving it requires explicit confirmation."
      : "Mode change is staged; save the policy to apply it.";
  } catch (error) {
    failed.value = true;
    message.value = error instanceof Error ? error.message : String(error);
  }
}
</script>

<template>
  <section
    class="panel remote-control"
    :class="{ 'remote-control--danger': isFullAccess }"
    aria-label="Remote control policy settings"
  >
    <header class="panel__header">
      <h2>Controlled Remote Access</h2>
      <span>{{ isFullAccess ? "DANGER: FULL ACCESS" : "Local pre-authorization" }}</span>
    </header>

    <div class="remote-control__body">
      <p class="remote-control__warning" :class="{ 'remote-control__warning--danger': isFullAccess }">
        <template v-if="isFullAccess">
          Full access lets authorized remote actors run arbitrary executables,
          shells, and interpreters with inherited environment variables, and read
          arbitrary UTF-8 files. Treat this as equivalent to local account access.
        </template>
        <template v-else>
          Scoped mode allows listed Gateway actors to read configured roots or run
          fixed executable profiles. Commands execute in Rust and are never sent
          to the renderer. Profile programs must use absolute executable paths.
        </template>
      </p>
      <label>
        <span>Mode</span>
        <select
          :value="editedMode ?? ''"
          :disabled="!available || loading || saving || editedMode === null"
          @change="setMode"
        >
          <option value="disabled">Disabled</option>
          <option value="scoped">Scoped</option>
          <option value="full_access">Full access (dangerous)</option>
        </select>
      </label>
      <label>
        <span>Policy JSON</span>
        <textarea
          v-model="source"
          rows="22"
          spellcheck="false"
          :disabled="!available || loading || saving"
        />
      </label>
      <p class="remote-control__note">
        Exec requests use <code>program</code>, <code>args</code>, and
        <code>cwd</code>. In scoped mode, program is a local profile id and cwd is
        relative to its root. File requests use <code>path</code>, optional
        <code>rootId</code>, <code>offset</code>, and <code>maxBytes</code>;
        scoped mode requires a root id and relative path. The Gateway must inject
        <code>actorAccountKey</code> for both capabilities.
      </p>
      <p class="remote-control__note">
        Computer Use is visual-only: it captures the virtual desktop and uses
        normalized coordinates, without UI Automation or DOM access. Set
        <code>computerUse.allowScreenCapture</code> and
        <code>computerUse.allowInput</code> independently. Current staged state:
        capture {{ editedComputerUse?.allowScreenCapture ? "on" : "off" }},
        input {{ editedComputerUse?.allowInput ? "on" : "off" }}.
      </p>
      <div class="remote-control__actions">
        <button
          class="settings-button settings-button--primary"
          type="button"
          :disabled="!available || loading || saving"
          @click="savePolicy"
        >
          {{ saving ? "Saving..." : "Save policy" }}
        </button>
        <button
          class="settings-button settings-button--quiet"
          type="button"
          :disabled="!available || loading || saving"
          @click="loadPolicy"
        >
          Reload
        </button>
      </div>
      <p
        v-if="message"
        class="remote-control__message"
        :class="{ 'remote-control__message--error': failed }"
        :role="failed ? 'alert' : 'status'"
      >
        {{ message }}
      </p>
    </div>
  </section>
</template>
