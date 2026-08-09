<script setup lang="ts">
import { invoke, isTauri } from "@tauri-apps/api/core";
import { onMounted, ref } from "vue";

import {
  defaultRemoteControlPolicy,
  parseRemoteControlPolicy,
  type RemoteControlPolicy,
} from "@/services/remoteControlPolicy";

const available = isTauri();
const source = ref(JSON.stringify(defaultRemoteControlPolicy, null, 2));
const loading = ref(false);
const saving = ref(false);
const message = ref(available ? "" : "Policy editing is available in the Tauri desktop app.");
const failed = ref(false);

onMounted(() => {
  if (available) void loadPolicy();
});

async function loadPolicy() {
  loading.value = true;
  failed.value = false;
  try {
    const policy = await invoke<RemoteControlPolicy>("remote_control_policy_read");
    source.value = JSON.stringify(policy, null, 2);
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
  saving.value = true;
  try {
    await invoke("remote_control_policy_save", { policy });
    source.value = JSON.stringify(policy, null, 2);
    message.value = "Local pre-authorization policy saved.";
  } catch (error) {
    failed.value = true;
    message.value = String(error);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="panel remote-control" aria-label="Remote control policy settings">
    <header class="panel__header">
      <h2>Controlled Remote Access</h2>
      <span>Local pre-authorization</span>
    </header>

    <div class="remote-control__body">
      <p class="remote-control__warning">
        Disabled by default. Enabling this policy allows only listed Gateway
        actor account keys to read configured roots or run fixed executable
        profiles. Commands execute in Rust and are never sent to the renderer.
        Profile programs must use absolute executable paths. Enabling additional
        arguments grants the agent the full argument surface of that executable;
        leave it disabled unless that program is safe with arbitrary arguments.
      </p>
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
        Profiles accept only <code>profileId</code>, optional additional
        <code>args</code>, and <code>cwdRelative</code>. File reads accept
        <code>rootId</code>, <code>relativePath</code>, <code>offset</code>, and
        <code>maxBytes</code>. The Gateway must inject
        <code>actorAccountKey</code> for both capabilities.
      </p>
      <div class="remote-control__actions">
        <button type="button" :disabled="!available || loading || saving" @click="savePolicy">
          {{ saving ? "Saving..." : "Save policy" }}
        </button>
        <button type="button" :disabled="!available || loading || saving" @click="loadPolicy">
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
