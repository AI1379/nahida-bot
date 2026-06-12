<script setup lang="ts">
import { currentDesktopSurface } from "@/services/desktopSurface";
import MainWindowView from "@/views/MainWindowView.vue";
import PetWindowView from "@/views/PetWindowView.vue";

const surface = currentDesktopSurface();
document.documentElement.dataset.window = surface;
</script>

<template>
  <PetWindowView v-if="surface === 'pet'" />
  <MainWindowView v-else />
</template>
