<script setup lang="ts">
import { invoke, isTauri } from "@tauri-apps/api/core";
import { computed, onMounted, ref } from "vue";

import {
  defaultRemoteControlPolicy,
  REMOTE_CONTROL_HARD_LIMITS,
  validateRemoteControlPolicy,
  type RemoteControlExecProfile,
  type RemoteControlMode,
  type RemoteControlPolicy,
} from "@/services/remoteControlPolicy";

const available = isTauri();
const loading = ref(false);
const saving = ref(false);
const message = ref(available ? "" : "Policy editing is available in the Tauri desktop app.");
const failed = ref(false);
const draft = ref<RemoteControlPolicy>(deepClone(defaultRemoteControlPolicy));
const loaded = ref<RemoteControlPolicy>(deepClone(defaultRemoteControlPolicy));

const controlsDisabled = computed(
  () => !available || loading.value || saving.value,
);
const isDirty = computed(
  () => JSON.stringify(draft.value) !== JSON.stringify(loaded.value),
);
const isFullAccess = computed(() => draft.value.mode === "full_access");
const modeStagingHint = computed(() => {
  if (draft.value.mode === loaded.value.mode) return "";
  return draft.value.mode === "full_access"
    ? "Full access is staged. Saving it requires explicit confirmation."
    : "Mode change is staged; save the policy to apply it.";
});
const actorsWarning = computed(() => {
  if (draft.value.mode === "disabled") return "";
  const hasActor = draft.value.allowedActorAccountKeys.some(
    (actor) => actor.trim() !== "",
  );
  return hasActor
    ? ""
    : "No actors are allowed yet. Until an actor key is listed, every remote request is rejected with actor_denied — even in full access mode.";
});
const actorsFull = computed(
  () =>
    draft.value.allowedActorAccountKeys.length >=
    REMOTE_CONTROL_HARD_LIMITS.maxActors,
);
const rootsFull = computed(
  () => draft.value.readRoots.length >= REMOTE_CONTROL_HARD_LIMITS.maxRoots,
);
const profilesFull = computed(
  () =>
    draft.value.execProfiles.length >= REMOTE_CONTROL_HARD_LIMITS.maxProfiles,
);

onMounted(() => {
  if (available) void loadPolicy();
});

async function loadPolicy() {
  loading.value = true;
  failed.value = false;
  try {
    const policy = await invoke<RemoteControlPolicy>(
      "remote_control_policy_read",
    );
    adoptPolicy(policy);
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
  const policy = deepClone(draft.value);
  const validationError = validateRemoteControlPolicy(policy);
  if (validationError) {
    failed.value = true;
    message.value = validationError;
    return;
  }
  if (
    policy.mode === "full_access" &&
    loaded.value.mode !== "full_access" &&
    !window.confirm(
      "Enable full access? Authorized remote actors will be able to run arbitrary programs and read arbitrary text files on this computer.",
    )
  ) {
    message.value = "Full access was not enabled.";
    return;
  }
  if (
    policy.computerUse.allowScreenCapture &&
    !loaded.value.computerUse.allowScreenCapture &&
    !window.confirm(
      "Enable screen capture? Authorized remote actors will be able to capture all visible pixels across the virtual desktop, which may include sensitive information.",
    )
  ) {
    message.value = "Screen capture was not enabled.";
    return;
  }
  if (
    policy.computerUse.allowInput &&
    !loaded.value.computerUse.allowInput &&
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
    loaded.value = deepClone(policy);
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
  draft.value.mode = mode;
}

function addActor() {
  if (!actorsFull.value) draft.value.allowedActorAccountKeys.push("");
}

function removeActor(index: number) {
  draft.value.allowedActorAccountKeys.splice(index, 1);
}

function setActor(index: number, event: Event) {
  draft.value.allowedActorAccountKeys[index] = (
    event.target as HTMLInputElement
  ).value;
}

function addRoot() {
  if (!rootsFull.value) draft.value.readRoots.push({ id: "", path: "" });
}

function removeRoot(index: number) {
  draft.value.readRoots.splice(index, 1);
}

function addProfile() {
  if (!profilesFull.value) {
    draft.value.execProfiles.push({
      id: "",
      program: "",
      fixedArgs: [],
      cwdRootId: "",
      allowAdditionalArgs: false,
    });
  }
}

function removeProfile(index: number) {
  draft.value.execProfiles.splice(index, 1);
}

function setFixedArgs(profile: RemoteControlExecProfile, event: Event) {
  const value = (event.target as HTMLTextAreaElement).value;
  profile.fixedArgs = value
    .split("\n")
    .filter((line) => line.trim() !== "");
}

function adoptPolicy(policy: RemoteControlPolicy) {
  const snapshot = deepClone(policy);
  draft.value = snapshot;
  loaded.value = deepClone(policy);
}

function deepClone(policy: RemoteControlPolicy): RemoteControlPolicy {
  return JSON.parse(JSON.stringify(policy));
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

      <label class="remote-control__field">
        <span>Mode</span>
        <select
          :value="draft.mode"
          :disabled="controlsDisabled"
          @change="setMode"
        >
          <option value="disabled">Disabled</option>
          <option value="scoped">Scoped</option>
          <option value="full_access">Full access (dangerous)</option>
        </select>
      </label>
      <p v-if="modeStagingHint" class="remote-control__note">
        {{ modeStagingHint }}
      </p>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>Allowed actors</span>
          <button
            class="settings-button settings-button--quiet"
            type="button"
            :disabled="controlsDisabled || actorsFull"
            @click="addActor"
          >
            Add actor
          </button>
        </div>
        <p v-if="actorsWarning" class="remote-control__note remote-control__note--warn">
          {{ actorsWarning }}
        </p>
        <ul v-if="draft.allowedActorAccountKeys.length" class="remote-control__list">
          <li
            v-for="(actor, index) in draft.allowedActorAccountKeys"
            :key="index"
            class="remote-control__row"
          >
            <input
              :value="actor"
              placeholder="milky:user:12345"
              spellcheck="false"
              :disabled="controlsDisabled"
              @input="setActor(index, $event)"
            />
            <button
              class="settings-button settings-button--quiet remote-control__remove"
              type="button"
              :disabled="controlsDisabled"
              @click="removeActor(index)"
            >
              Remove
            </button>
          </li>
        </ul>
        <p v-else class="remote-control__note">
          The whitelist is empty. Actor keys look like
          <code>milky:user:12345</code>; pairing this desktop adds its bound
          actor automatically.
        </p>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>Read roots</span>
          <button
            class="settings-button settings-button--quiet"
            type="button"
            :disabled="controlsDisabled || rootsFull"
            @click="addRoot"
          >
            Add root
          </button>
        </div>
        <ul v-if="draft.readRoots.length" class="remote-control__list">
          <li
            v-for="(root, index) in draft.readRoots"
            :key="index"
            class="remote-control__root"
          >
            <label class="remote-control__field">
              <span>Id</span>
              <input
                v-model="root.id"
                placeholder="notes"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <label class="remote-control__field remote-control__field--grow">
              <span>Path (absolute)</span>
              <input
                v-model="root.path"
                placeholder="C:\Users\me\notes"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <button
              class="settings-button settings-button--quiet remote-control__remove"
              type="button"
              :disabled="controlsDisabled"
              @click="removeRoot(index)"
            >
              Remove
            </button>
          </li>
        </ul>
        <p v-else class="remote-control__note">
          Scoped file reads and profile working directories stay inside these
          roots. Not used in full access mode.
        </p>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>Exec profiles</span>
          <button
            class="settings-button settings-button--quiet"
            type="button"
            :disabled="controlsDisabled || profilesFull"
            @click="addProfile"
          >
            Add profile
          </button>
        </div>
        <p v-if="!draft.execProfiles.length" class="remote-control__note">
          In scoped mode the Gateway can only run programs listed here. Not used
          in full access mode.
        </p>
        <div
          v-for="(profile, index) in draft.execProfiles"
          :key="index"
          class="remote-control__profile"
        >
          <div class="remote-control__section-head">
            <span>Profile {{ index + 1 }}</span>
            <button
              class="settings-button settings-button--quiet remote-control__remove"
              type="button"
              :disabled="controlsDisabled"
              @click="removeProfile(index)"
            >
              Remove
            </button>
          </div>
          <div class="remote-control__grid">
            <label class="remote-control__field">
              <span>Id</span>
              <input
                v-model="profile.id"
                placeholder="rust-version"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <label class="remote-control__field">
              <span>Program (absolute path)</span>
              <input
                v-model="profile.program"
                placeholder="C:\Tools\my-tool.exe"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <label class="remote-control__field">
              <span>Working directory root</span>
              <select
                v-model="profile.cwdRootId"
                :disabled="controlsDisabled"
              >
                <option value="" disabled>Select a read root</option>
                <option
                  v-for="root in draft.readRoots"
                  :key="root.id"
                  :value="root.id"
                >
                  {{ root.id || "(unnamed root)" }}
                </option>
              </select>
            </label>
          </div>
          <label class="remote-control__field">
            <span>Fixed arguments (one per line)</span>
            <textarea
              :value="profile.fixedArgs.join('\n')"
              rows="3"
              spellcheck="false"
              :disabled="controlsDisabled"
              @change="setFixedArgs(profile, $event)"
            />
          </label>
          <label class="remote-control__check">
            <input
              type="checkbox"
              :checked="profile.allowAdditionalArgs"
              :disabled="controlsDisabled"
              @change="profile.allowAdditionalArgs = !profile.allowAdditionalArgs"
            />
            <span>Allow the Gateway to append additional arguments</span>
          </label>
        </div>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>Computer use</span>
        </div>
        <label class="remote-control__check">
          <input
            type="checkbox"
            :checked="draft.computerUse.allowScreenCapture"
            :disabled="controlsDisabled"
            @change="draft.computerUse.allowScreenCapture = !draft.computerUse.allowScreenCapture"
          />
          <span>Allow screen capture (all visible pixels, no DOM or UI Automation)</span>
        </label>
        <label class="remote-control__check">
          <input
            type="checkbox"
            :checked="draft.computerUse.allowInput"
            :disabled="controlsDisabled"
            @change="draft.computerUse.allowInput = !draft.computerUse.allowInput"
          />
          <span>Allow pointer and keyboard input with normalized coordinates</span>
        </label>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>Limits</span>
        </div>
        <div class="remote-control__grid">
          <label class="remote-control__field">
            <span>Timeout (ms)</span>
            <input
              v-model.number="draft.limits.timeoutMs"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.timeoutMs"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>Output limit (bytes)</span>
            <input
              v-model.number="draft.limits.outputLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>Stdout limit (bytes)</span>
            <input
              v-model.number="draft.limits.stdoutLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>Stderr limit (bytes)</span>
            <input
              v-model.number="draft.limits.stderrLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>File limit (bytes)</span>
            <input
              v-model.number="draft.limits.fileLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.fileLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>Max additional args</span>
            <input
              v-model.number="draft.limits.maxAdditionalArgs"
              type="number"
              min="0"
              :max="REMOTE_CONTROL_HARD_LIMITS.maxAdditionalArgs"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>Max bytes per arg</span>
            <input
              v-model.number="draft.limits.maxArgBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.maxArgBytes"
              :disabled="controlsDisabled"
            />
          </label>
        </div>
      </div>

      <p class="remote-control__note">
        Exec requests use <code>program</code>, <code>args</code>, and
        <code>cwd</code>. In scoped mode, program is a local profile id and cwd is
        relative to its root. File requests use <code>path</code>, optional
        <code>rootId</code>, <code>offset</code>, and <code>maxBytes</code>;
        scoped mode requires a root id and relative path. The Gateway must inject
        <code>actorAccountKey</code> for both capabilities.
      </p>

      <div class="remote-control__actions">
        <button
          class="settings-button settings-button--primary"
          type="button"
          :disabled="controlsDisabled || !isDirty"
          @click="savePolicy"
        >
          {{ saving ? "Saving..." : "Save policy" }}
        </button>
        <button
          class="settings-button settings-button--quiet"
          type="button"
          :disabled="controlsDisabled"
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
