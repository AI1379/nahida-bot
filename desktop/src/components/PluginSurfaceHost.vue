<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";

import type {
  PluginSurfaceContribution,
  PluginSurfaceTarget,
} from "@/domain/pluginSurface";
import { selectPluginSurfaces } from "@/domain/pluginSurface";

const props = defineProps<{
  surfaces: PluginSurfaceContribution[];
  target: PluginSurfaceTarget;
}>();

const visibleSurfaces = computed(() =>
  selectPluginSurfaces(props.surfaces, props.target),
);

const nowMs = ref(Date.now());
const ticker = setInterval(() => {
  nowMs.value = Date.now();
}, 1000);

onUnmounted(() => clearInterval(ticker));

function countdownLabel(surface: PluginSurfaceContribution): string {
  if (!surface.view.expiresAt) return "--:--";
  const deadline = Date.parse(surface.view.expiresAt);
  if (!Number.isFinite(deadline)) return "--:--";
  const remaining = Math.max(0, Math.round((deadline - nowMs.value) / 1000));
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
</script>

<template>
  <div
    v-if="visibleSurfaces.length"
    class="plugin-surface-host"
    :data-target="target"
  >
    <article
      v-for="surface in visibleSurfaces"
      :key="`${surface.ownerPluginId}:${surface.id}`"
      class="plugin-surface"
      :data-kind="surface.kind"
      :data-tone="surface.view.tone"
    >
      <span
        v-if="surface.kind === 'badge' || surface.kind === 'countdown'"
        class="plugin-surface__dot"
      />
      <div class="plugin-surface__body">
        <strong v-if="surface.view.title">{{ surface.view.title }}</strong>
        <span v-if="surface.view.status" class="plugin-surface__status">
          {{ surface.view.status }}
        </span>
        <p v-if="surface.view.text">{{ surface.view.text }}</p>
        <ul v-if="surface.view.items.length" class="plugin-surface__items">
          <li
            v-for="(item, index) in surface.view.items"
            :key="`${surface.id}-${index}`"
            :data-completed="item.completed"
          >
            <span>{{ item.text }}</span>
            <small v-if="item.detail">{{ item.detail }}</small>
          </li>
        </ul>
        <div
          v-if="surface.kind === 'progress' && surface.view.progress !== null"
          class="plugin-surface__progress"
          role="progressbar"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="Math.round(surface.view.progress * 100)"
        >
          <span :style="{ width: `${surface.view.progress * 100}%` }" />
        </div>
      </div>
      <span
        v-if="surface.kind === 'countdown'"
        class="plugin-surface__countdown"
      >
        {{ countdownLabel(surface) }}
      </span>
      <small v-if="surface.view.detail" class="plugin-surface__detail">
        {{ surface.view.detail }}
      </small>
    </article>
  </div>
</template>
