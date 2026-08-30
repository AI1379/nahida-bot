<script setup lang="ts">
import type { PerformanceMode, PetWindowEdge } from "@/domain/config";
import { useDesktopStore } from "@/stores/desktop";

const store = useDesktopStore();

function selectModel(event: Event) {
  store.selectModel((event.target as HTMLSelectElement).value);
}

function updatePerformanceMode(event: Event) {
  store.updatePerformanceMode(
    (event.target as HTMLSelectElement).value as PerformanceMode,
  );
}

function updateEdge(event: Event) {
  store.updateDesktopWindowState({
    edge: (event.target as HTMLSelectElement).value as PetWindowEdge,
  });
}

function updateNumber(
  field: "width" | "height" | "exposedPx",
  event: Event,
) {
  store.updateDesktopWindowState({
    [field]: Number((event.target as HTMLInputElement).value),
  });
}
</script>

<template>
  <section class="panel desktop-pet-settings" aria-label="桌宠外观与性能">
    <header class="panel__header">
      <h2>桌宠外观</h2>
      <span>位置、尺寸与性能</span>
    </header>

    <div class="desktop-pet-settings__body">
      <label class="desktop-pet-settings__field desktop-pet-settings__field--wide">
        <span>Live2D 模型</span>
        <select :value="store.selectedModelId" @change="selectModel">
          <option
            v-for="model in store.models"
            :key="model.id"
            :value="model.id"
          >
            {{ model.name }}
          </option>
        </select>
      </label>

      <label class="desktop-pet-settings__field">
        <span>贴边位置</span>
        <select :value="store.localConfig.windowState.edge" @change="updateEdge">
          <option value="right">右侧</option>
          <option value="left">左侧</option>
          <option value="bottom">底部</option>
          <option value="top">顶部</option>
        </select>
      </label>

      <label class="desktop-pet-settings__field">
        <span>性能模式</span>
        <select
          :value="store.localConfig.performanceMode"
          @change="updatePerformanceMode"
        >
          <option value="power_saver">省电</option>
          <option value="balanced">平衡</option>
          <option value="active">高流畅度</option>
        </select>
      </label>

      <label class="desktop-pet-settings__field">
        <span>窗口宽度</span>
        <div class="desktop-pet-settings__number">
          <input
            type="number"
            min="280"
            max="720"
            step="10"
            :value="store.localConfig.windowState.width"
            @change="updateNumber('width', $event)"
          />
          <small>px</small>
        </div>
      </label>

      <label class="desktop-pet-settings__field">
        <span>窗口高度</span>
        <div class="desktop-pet-settings__number">
          <input
            type="number"
            min="360"
            max="900"
            step="10"
            :value="store.localConfig.windowState.height"
            @change="updateNumber('height', $event)"
          />
          <small>px</small>
        </div>
      </label>

      <label class="desktop-pet-settings__field desktop-pet-settings__field--wide">
        <span>收起时露出 {{ store.localConfig.windowState.exposedPx }} px</span>
        <input
          type="range"
          min="16"
          max="160"
          step="2"
          :value="store.localConfig.windowState.exposedPx"
          @input="updateNumber('exposedPx', $event)"
        />
      </label>

      <label class="desktop-pet-settings__check desktop-pet-settings__field--wide">
        <input
          type="checkbox"
          :checked="store.localConfig.windowState.alwaysOnTop"
          @change="store.updateDesktopWindowState({
            alwaysOnTop: ($event.target as HTMLInputElement).checked,
          })"
        />
        <span>让桌宠始终显示在其他窗口上方</span>
      </label>

      <p class="desktop-pet-settings__note desktop-pet-settings__field--wide">
        设置会立即保存到本机。隐藏状态下调整尺寸或贴边位置，也会同步到桌宠窗口。
      </p>
    </div>
  </section>
</template>
