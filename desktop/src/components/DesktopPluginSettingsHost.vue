<script setup lang="ts">
import { computed } from "vue";

import type {
  DesktopPluginHost,
  DesktopPluginSettingsPlacement,
} from "@/plugins/desktopPluginHost";

const props = defineProps<{
  host: DesktopPluginHost;
  placement: DesktopPluginSettingsPlacement;
  sectionId?: string;
}>();

const panels = computed(() =>
  props.host.settingsPanels(props.placement, props.sectionId),
);
</script>

<template>
  <component
    :is="panel.contribution.component"
    v-for="panel in panels"
    :key="`${panel.ownerPluginId}:${panel.contribution.id}`"
    :host="props.host"
    :plugin-id="panel.ownerPluginId"
    :runtime="panel.runtime"
  />
</template>
