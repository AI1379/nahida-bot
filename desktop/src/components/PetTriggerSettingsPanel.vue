<script setup lang="ts">
import type { PetTriggerSettings } from "@/domain/config";

const props = defineProps<{
  settings: PetTriggerSettings;
}>();

const emit = defineEmits<{
  update: [settings: PetTriggerSettings];
}>();

function update(patch: Partial<PetTriggerSettings>) {
  emit("update", {
    ...props.settings,
    ...patch,
  });
}

function changeDistance(
  key: "wakeDistancePx" | "hideDistancePx",
  event: Event,
) {
  update({ [key]: Number((event.target as HTMLInputElement).value) });
}

function changeDuration(
  key: "autoRetreatMs" | "chatIdleTimeoutMs",
  event: Event,
) {
  update({
    [key]: Number((event.target as HTMLInputElement).value) * 1000,
  });
}
</script>

<template>
  <section class="panel pet-trigger-settings" aria-label="桌宠触发设置">
    <header class="panel__header">
      <h2>桌宠触发方式</h2>
      <span>靠近感应与空闲计时</span>
    </header>

    <div class="pet-trigger-settings__body">
      <label>
        <span>唤醒距离：{{ settings.wakeDistancePx }} px</span>
        <input
          type="range"
          min="8"
          max="240"
          step="2"
          :value="settings.wakeDistancePx"
          @input="changeDistance('wakeDistancePx', $event)"
        />
      </label>

      <label>
        <span>收起距离：{{ settings.hideDistancePx }} px</span>
        <input
          type="range"
          min="16"
          max="480"
          step="2"
          :value="settings.hideDistancePx"
          @input="changeDistance('hideDistancePx', $event)"
        />
      </label>

      <label>
        <span>
          自动收起：{{ Math.round(settings.autoRetreatMs / 1000) }} 秒
        </span>
        <input
          type="range"
          min="2"
          max="120"
          step="1"
          :value="Math.round(settings.autoRetreatMs / 1000)"
          @input="changeDuration('autoRetreatMs', $event)"
        />
      </label>

      <label>
        <span>
          对话空闲超时：
          {{ Math.round(settings.chatIdleTimeoutMs / 1000) }} 秒
        </span>
        <input
          type="range"
          min="10"
          max="300"
          step="5"
          :value="Math.round(settings.chatIdleTimeoutMs / 1000)"
          @input="changeDuration('chatIdleTimeoutMs', $event)"
        />
      </label>

      <p class="pet-trigger-settings__note">
        鼠标靠近到唤醒距离时，隐藏的桌宠会探出；离开到收起距离时会再次隐藏。
        收起距离会始终大于唤醒距离，避免桌宠反复闪动。
      </p>
    </div>
  </section>
</template>
