<script setup lang="ts">
import MainWindowView from "@/views/MainWindowView.vue";
import PetWindowView from "@/views/PetWindowView.vue";

const surface = document.documentElement.dataset.window === "pet"
  ? "pet"
  : "main";
</script>

<template>
  <PetWindowView v-if="surface === 'pet'" />
  <MainWindowView v-else />
</template>
