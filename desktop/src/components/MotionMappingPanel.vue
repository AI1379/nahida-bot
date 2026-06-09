<script setup lang="ts">
import { computed } from "vue";

import { displayMotions } from "@/domain/displayPlan";
import type { DisplayMotion } from "@/domain/displayPlan";
import type {
  Live2DModelManifest,
  Live2DMotionOption,
  Live2DMotionTarget,
} from "@/domain/live2d";

const props = defineProps<{
  model: Live2DModelManifest;
  motions: Live2DMotionOption[];
}>();

const emit = defineEmits<{
  updateMapping: [motion: DisplayMotion, target: Live2DMotionTarget];
  preview: [motion: DisplayMotion];
}>();

interface MotionSelectOption {
  value: string;
  label: string;
  detail: string;
  target: Live2DMotionTarget;
}

const motionOptions = computed(() => {
  const options = new Map<string, MotionSelectOption>();
  const none: MotionSelectOption = {
    value: "none",
    label: "None",
    detail: "do nothing",
    target: { source: "none" },
  };
  options.set(none.value, none);

  for (const motion of props.motions) {
    let target: Live2DMotionTarget;
    if (motion.source === "procedural") {
      if (!motion.motion) continue;
      target = {
        source: "procedural",
        motion: motion.motion,
      };
    } else {
      target = {
        source: "model",
        group: motion.group,
        index: motion.index,
      };
    }
    const option: MotionSelectOption = {
      value: encodeTarget(target),
      label:
        motion.source === "procedural"
          ? `Base ${motion.name}`
          : `${motion.group} #${motion.index}`,
      detail: motion.file,
      target,
    };
    options.set(option.value, option);
  }

  for (const target of Object.values(props.model.motionMap)) {
    if (!target) continue;
    const value = encodeTarget(target);
    if (!options.has(value)) {
      options.set(value, {
        value,
        label: targetLabel(target),
        detail: "saved mapping",
        target,
      });
    }
  }

  return Array.from(options.values());
});

function encodeTarget(target: Live2DMotionTarget): string {
  switch (target.source) {
    case "none":
      return "none";
    case "procedural":
      return `procedural:${target.motion}`;
    case "model":
      return `model:${encodeURIComponent(target.group)}:${target.index}`;
  }
}

function targetFromValue(value: string): Live2DMotionTarget {
  if (value === "none") return { source: "none" };

  const [source, rawGroupOrMotion, rawIndex] = value.split(":");
  if (source === "procedural" && isDisplayMotion(rawGroupOrMotion)) {
    return {
      source: "procedural",
      motion: rawGroupOrMotion,
    };
  }
  if (source === "model" && rawGroupOrMotion && rawIndex) {
    const index = Number(rawIndex);
    if (Number.isInteger(index) && index >= 0) {
      return {
        source: "model",
        group: decodeURIComponent(rawGroupOrMotion),
        index,
      };
    }
  }

  return { source: "none" };
}

function isDisplayMotion(value: string | undefined): value is DisplayMotion {
  return displayMotions.includes(value as DisplayMotion);
}

function currentTarget(motion: DisplayMotion): Live2DMotionTarget {
  return props.model.motionMap[motion] ?? {
    source: motion === "idle" ? "none" : "procedural",
    motion,
  };
}

function currentValue(motion: DisplayMotion): string {
  return encodeTarget(currentTarget(motion));
}

function targetLabel(target: Live2DMotionTarget): string {
  switch (target.source) {
    case "none":
      return "None";
    case "procedural":
      return `Base ${target.motion}`;
    case "model":
      return `${target.group} #${target.index}`;
  }
}

function optionLabel(option: MotionSelectOption): string {
  return option.detail ? `${option.label} (${option.detail})` : option.label;
}

function updateMapping(motion: DisplayMotion, event: Event) {
  const target = event.target as HTMLSelectElement;
  emit("updateMapping", motion, targetFromValue(target.value));
}
</script>

<template>
  <section class="panel motion-map" aria-label="Motion mapping">
    <header class="panel__header">
      <h2>Motion Map</h2>
      <span>{{ motionOptions.length }} targets</span>
    </header>

    <ul class="motion-map__list">
      <li v-for="motion in displayMotions" :key="motion">
        <div class="motion-map__label">
          <strong>{{ motion }}</strong>
          <span>{{ targetLabel(currentTarget(motion)) }}</span>
        </div>
        <div class="motion-map__controls">
          <select
            :id="`motion-map-${motion}`"
            :value="currentValue(motion)"
            @change="updateMapping(motion, $event)"
          >
            <option
              v-for="option in motionOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ optionLabel(option) }}
            </option>
          </select>
          <button type="button" @click="emit('preview', motion)">Test</button>
        </div>
      </li>
    </ul>
  </section>
</template>
