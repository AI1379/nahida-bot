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
const message = ref(available ? "" : "远程控制策略仅可在 Tauri 桌面应用中编辑。");
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
    ? "已选择完全访问；保存时还需再次确认。"
    : "模式更改尚未生效，请保存策略。";
});
const actorsWarning = computed(() => {
  if (draft.value.mode === "disabled") return "";
  const hasActor = draft.value.allowedActorAccountKeys.some(
    (actor) => actor.trim() !== "",
  );
  return hasActor
    ? ""
    : "尚未允许任何账号。添加账号前，所有远程请求都会被拒绝，即使在完全访问模式下也是如此。";
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
    message.value = "已从本地安全存储读取策略。";
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
      "启用完全访问？获授权的远程账号将能运行任意程序并读取这台电脑上的任意文本文件。",
    )
  ) {
    message.value = "未启用完全访问。";
    return;
  }
  if (
    policy.computerUse.allowScreenCapture &&
    !loaded.value.computerUse.allowScreenCapture &&
    !window.confirm(
      "允许屏幕捕获？获授权的远程账号将能截取整个虚拟桌面的可见内容，其中可能包含敏感信息。",
    )
  ) {
    message.value = "未启用屏幕捕获。";
    return;
  }
  if (
    policy.computerUse.allowInput &&
    !loaded.value.computerUse.allowInput &&
    !window.confirm(
      "允许控制输入？获授权的远程账号将能移动鼠标、点击、滚动、输入文本和按键。",
    )
  ) {
    message.value = "未启用输入控制。";
    return;
  }
  saving.value = true;
  try {
    await invoke("remote_control_policy_save", { policy });
    loaded.value = deepClone(policy);
    message.value = "本地预授权策略已保存。";
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
    aria-label="远程控制策略设置"
  >
    <header class="panel__header">
      <h2>受控远程访问</h2>
      <span>{{ isFullAccess ? "危险：完全访问" : "本地预授权" }}</span>
    </header>

    <div class="remote-control__body">
      <p class="remote-control__warning" :class="{ 'remote-control__warning--danger': isFullAccess }">
        <template v-if="isFullAccess">
          完全访问允许获授权的远程账号运行任意可执行文件、Shell 和解释器，
          并读取任意 UTF-8 文件。请把它视为与本地账号访问等同的高风险权限。
        </template>
        <template v-else>
          受限模式仅允许名单内的 Gateway 账号读取指定目录或运行固定程序。
          命令在 Rust 层执行，不会发送给渲染器；程序必须使用绝对路径。
        </template>
      </p>

      <label class="remote-control__field">
        <span>模式</span>
        <select
          :value="draft.mode"
          :disabled="controlsDisabled"
          @change="setMode"
        >
          <option value="disabled">关闭</option>
          <option value="scoped">受限</option>
          <option value="full_access">完全访问（危险）</option>
        </select>
      </label>
      <p v-if="modeStagingHint" class="remote-control__note">
        {{ modeStagingHint }}
      </p>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>允许的账号</span>
          <button
            class="settings-button settings-button--quiet"
            type="button"
            :disabled="controlsDisabled || actorsFull"
            @click="addActor"
          >
            添加账号
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
              移除
            </button>
          </li>
        </ul>
        <p v-else class="remote-control__note">
          白名单为空。账号键格式如 <code>milky:user:12345</code>；
          配对桌面端时会自动添加其绑定账号。
        </p>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>可读目录</span>
          <button
            class="settings-button settings-button--quiet"
            type="button"
            :disabled="controlsDisabled || rootsFull"
            @click="addRoot"
          >
            添加目录
          </button>
        </div>
        <ul v-if="draft.readRoots.length" class="remote-control__list">
          <li
            v-for="(root, index) in draft.readRoots"
            :key="index"
            class="remote-control__root"
          >
            <label class="remote-control__field">
              <span>ID</span>
              <input
                v-model="root.id"
                placeholder="notes"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <label class="remote-control__field remote-control__field--grow">
              <span>路径（绝对路径）</span>
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
              移除
            </button>
          </li>
        </ul>
        <p v-else class="remote-control__note">
          受限模式下的文件读取与程序工作目录必须位于这些目录中；完全访问模式不会使用此限制。
        </p>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>程序配置</span>
          <button
            class="settings-button settings-button--quiet"
            type="button"
            :disabled="controlsDisabled || profilesFull"
            @click="addProfile"
          >
            添加配置
          </button>
        </div>
        <p v-if="!draft.execProfiles.length" class="remote-control__note">
          受限模式下，Gateway 只能运行这里列出的程序；完全访问模式不会使用此限制。
        </p>
        <div
          v-for="(profile, index) in draft.execProfiles"
          :key="index"
          class="remote-control__profile"
        >
          <div class="remote-control__section-head">
            <span>配置 {{ index + 1 }}</span>
            <button
              class="settings-button settings-button--quiet remote-control__remove"
              type="button"
              :disabled="controlsDisabled"
              @click="removeProfile(index)"
            >
              移除
            </button>
          </div>
          <div class="remote-control__grid">
            <label class="remote-control__field">
              <span>ID</span>
              <input
                v-model="profile.id"
                placeholder="rust-version"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <label class="remote-control__field">
              <span>程序（绝对路径）</span>
              <input
                v-model="profile.program"
                placeholder="C:\Tools\my-tool.exe"
                spellcheck="false"
                :disabled="controlsDisabled"
              />
            </label>
            <label class="remote-control__field">
              <span>工作目录根路径</span>
              <select
                v-model="profile.cwdRootId"
                :disabled="controlsDisabled"
              >
                <option value="" disabled>选择可读目录</option>
                <option
                  v-for="root in draft.readRoots"
                  :key="root.id"
                  :value="root.id"
                >
                  {{ root.id || "（未命名目录）" }}
                </option>
              </select>
            </label>
          </div>
          <label class="remote-control__field">
            <span>固定参数（每行一个）</span>
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
            <span>允许 Gateway 追加参数</span>
          </label>
        </div>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>电脑操作</span>
        </div>
        <label class="remote-control__check">
          <input
            type="checkbox"
            :checked="draft.computerUse.allowScreenCapture"
            :disabled="controlsDisabled"
            @change="draft.computerUse.allowScreenCapture = !draft.computerUse.allowScreenCapture"
          />
          <span>允许屏幕捕获（所有可见像素，不含 DOM 或 UI Automation）</span>
        </label>
        <label class="remote-control__check">
          <input
            type="checkbox"
            :checked="draft.computerUse.allowInput"
            :disabled="controlsDisabled"
            @change="draft.computerUse.allowInput = !draft.computerUse.allowInput"
          />
          <span>允许通过归一化坐标控制鼠标和键盘</span>
        </label>
      </div>

      <div class="remote-control__section">
        <div class="remote-control__section-head">
          <span>限制</span>
        </div>
        <div class="remote-control__grid">
          <label class="remote-control__field">
            <span>超时（毫秒）</span>
            <input
              v-model.number="draft.limits.timeoutMs"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.timeoutMs"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>输出上限（字节）</span>
            <input
              v-model.number="draft.limits.outputLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>标准输出上限（字节）</span>
            <input
              v-model.number="draft.limits.stdoutLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>错误输出上限（字节）</span>
            <input
              v-model.number="draft.limits.stderrLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>文件上限（字节）</span>
            <input
              v-model.number="draft.limits.fileLimitBytes"
              type="number"
              min="1"
              :max="REMOTE_CONTROL_HARD_LIMITS.fileLimitBytes"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>追加参数数量上限</span>
            <input
              v-model.number="draft.limits.maxAdditionalArgs"
              type="number"
              min="0"
              :max="REMOTE_CONTROL_HARD_LIMITS.maxAdditionalArgs"
              :disabled="controlsDisabled"
            />
          </label>
          <label class="remote-control__field">
            <span>单个参数字节上限</span>
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
        程序请求使用 <code>program</code>、<code>args</code> 和 <code>cwd</code>。
        受限模式下，program 是本地配置 ID，cwd 是相对其根目录的路径。
        文件请求使用 <code>path</code>，以及可选的 <code>rootId</code>、
        <code>offset</code> 和 <code>maxBytes</code>；两类能力都需要 Gateway 注入
        <code>actorAccountKey</code>。
      </p>

      <div class="remote-control__actions">
        <button
          class="settings-button settings-button--primary"
          type="button"
          :disabled="controlsDisabled || !isDirty"
          @click="savePolicy"
        >
          {{ saving ? "正在保存…" : "保存策略" }}
        </button>
        <button
          class="settings-button settings-button--quiet"
          type="button"
          :disabled="controlsDisabled"
          @click="loadPolicy"
        >
          重新读取
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
