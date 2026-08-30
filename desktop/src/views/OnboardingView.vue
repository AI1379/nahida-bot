<script setup lang="ts">
defineEmits<{
  openSettings: [];
  useOfflineDemo: [];
}>();
</script>

<template>
  <section class="onboarding-view" aria-labelledby="onboarding-title">
    <div class="onboarding-view__art" aria-hidden="true">
      <span class="onboarding-view__leaf onboarding-view__leaf--left" />
      <span class="onboarding-view__core">✦</span>
      <span class="onboarding-view__leaf onboarding-view__leaf--right" />
    </div>
    <div class="onboarding-view__content">
      <p class="onboarding-view__eyebrow">欢迎使用 Nahida Desktop</p>
      <h2 id="onboarding-title">让纳西妲来到你的桌面</h2>
      <p>
        连接正在运行的 Nahida Gateway 后，就可以在桌面接收消息、语音和提醒。
        如果只是想先看看效果，也可以进入离线体验。
      </p>
      <div class="onboarding-view__actions">
        <button
          type="button"
          class="onboarding-view__primary"
          @click="$emit('openSettings')"
        >
          连接 Nahida Gateway
        </button>
        <button type="button" @click="$emit('useOfflineDemo')">
          进入离线体验
        </button>
      </div>
      <small>连接信息与凭据只保存在这台设备上。</small>
    </div>
  </section>
</template>
