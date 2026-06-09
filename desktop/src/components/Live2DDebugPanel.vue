<script setup lang="ts">
import { computed, ref } from "vue";

import type { DisplayMotion } from "@/domain/displayPlan";
import type { Live2DDebugSnapshot } from "@/renderers/live2dRenderer";

const props = defineProps<{
  snapshot: Live2DDebugSnapshot | null;
}>();

const emit = defineEmits<{
  close: [];
  refresh: [];
  resetAll: [];
  setPartOpacity: [payload: { index: number; opacity: number }];
  resetPartOpacity: [index: number];
  setParameterValue: [payload: { index: number; value: number }];
  resetParameterValue: [index: number];
  setExpression: [name: string];
  resetExpression: [];
  playMotion: [payload: {
    source: "model" | "procedural";
    group: string;
    index: number;
    motion?: DisplayMotion;
  }];
}>();

const activeTab = ref<"playback" | "parts" | "parameters" | "drawables">(
  "playback",
);
const query = ref("");

const normalizedQuery = computed(() => query.value.trim().toLowerCase());

const filteredParts = computed(() => {
  const parts = props.snapshot?.parts ?? [];
  if (!normalizedQuery.value) return parts;
  return parts.filter((part) =>
    `${part.index} ${part.id}`.toLowerCase().includes(normalizedQuery.value),
  );
});

const filteredParameters = computed(() => {
  const parameters = props.snapshot?.parameters ?? [];
  if (!normalizedQuery.value) return parameters;
  return parameters.filter((parameter) =>
    `${parameter.index} ${parameter.id}`
      .toLowerCase()
      .includes(normalizedQuery.value),
  );
});

const filteredDrawables = computed(() => {
  const drawables = props.snapshot?.drawables ?? [];
  if (!normalizedQuery.value) return drawables;
  return drawables.filter((drawable) =>
    `${drawable.index} ${drawable.id}`
      .toLowerCase()
      .includes(normalizedQuery.value),
  );
});

const filteredExpressions = computed(() => {
  const expressions = props.snapshot?.expressions ?? [];
  if (!normalizedQuery.value) return expressions;
  return expressions.filter((expression) =>
    `${expression.index} ${expression.name} ${expression.file}`
      .toLowerCase()
      .includes(normalizedQuery.value),
  );
});

const filteredMotions = computed(() => {
  const motions = props.snapshot?.motions ?? [];
  if (!normalizedQuery.value) return motions;
  return motions.filter((motion) =>
    `${motion.source} ${motion.group} ${motion.index} ${motion.name} ${motion.file}`
      .toLowerCase()
      .includes(normalizedQuery.value),
  );
});

function formatNumber(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return "n/a";
  return value.toFixed(digits);
}

function readNumericInput(event: Event): number {
  const target = event.target as HTMLInputElement;
  return Number(target.value);
}
</script>

<template>
  <aside class="live2d-debug" aria-label="Live2D debug panel">
    <header class="live2d-debug__header">
      <div>
        <strong>Live2D Debug</strong>
        <span v-if="props.snapshot">
          {{ props.snapshot.parts.length }} parts /
          {{ props.snapshot.drawables.length }} drawables /
          {{ props.snapshot.expressions.length }} expressions /
          {{ props.snapshot.motions.length }} motions
        </span>
      </div>
      <button type="button" @click="emit('close')">Close</button>
    </header>

    <div class="live2d-debug__toolbar">
      <button
        type="button"
        :class="{ 'is-active': activeTab === 'playback' }"
        @click="activeTab = 'playback'"
      >
        Playback
      </button>
      <button
        type="button"
        :class="{ 'is-active': activeTab === 'parts' }"
        @click="activeTab = 'parts'"
      >
        Parts
      </button>
      <button
        type="button"
        :class="{ 'is-active': activeTab === 'parameters' }"
        @click="activeTab = 'parameters'"
      >
        Params
      </button>
      <button
        type="button"
        :class="{ 'is-active': activeTab === 'drawables' }"
        @click="activeTab = 'drawables'"
      >
        Drawables
      </button>
    </div>

    <div class="live2d-debug__actions">
      <input v-model="query" type="search" placeholder="Filter id or index" />
      <button type="button" @click="emit('refresh')">Refresh</button>
      <button type="button" @click="emit('resetAll')">Reset</button>
    </div>

    <div v-if="!props.snapshot" class="live2d-debug__empty">
      Live2D runtime is not ready.
    </div>

    <div v-else class="live2d-debug__body">
      <div v-if="activeTab === 'playback'" class="debug-playback">
        <section>
          <header class="debug-section__header">
            <strong>Expressions</strong>
            <button type="button" @click="emit('resetExpression')">
              Reset Expression
            </button>
          </header>
          <div class="debug-button-grid">
            <button
              v-for="expression in filteredExpressions"
              :key="expression.index"
              type="button"
              @click="emit('setExpression', expression.name)"
            >
              <strong>{{ expression.name }}</strong>
              <span v-if="expression.file && expression.file !== expression.name">
                {{ expression.file }}
              </span>
            </button>
          </div>
        </section>

        <section>
          <header class="debug-section__header">
            <strong>Motions</strong>
            <span>{{ filteredMotions.length }} available</span>
          </header>
          <div class="debug-button-grid">
            <button
              v-for="motion in filteredMotions"
              :key="`${motion.source}:${motion.group}:${motion.index}`"
              type="button"
              @click="
                emit('playMotion', {
                  source: motion.source,
                  group: motion.group,
                  index: motion.index,
                  motion: motion.motion,
                })
              "
            >
              <strong>
                {{
                  motion.source === "procedural"
                    ? `Base ${motion.name}`
                    : motion.name
                }}
              </strong>
              <span v-if="motion.file">{{ motion.file }}</span>
            </button>
          </div>
        </section>
      </div>

      <ol v-else-if="activeTab === 'parts'" class="debug-list">
        <li
          v-for="part in filteredParts"
          :key="part.index"
          :class="{ 'is-overridden': part.overridden }"
        >
          <div class="debug-list__meta">
            <strong>#{{ part.index }} {{ part.id }}</strong>
            <span>opacity {{ formatNumber(part.opacity) }}</span>
          </div>
          <div class="debug-list__controls">
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              :value="part.opacity"
              @input="
                emit('setPartOpacity', {
                  index: part.index,
                  opacity: readNumericInput($event),
                })
              "
            />
            <button
              type="button"
              @click="
                emit('setPartOpacity', {
                  index: part.index,
                  opacity: part.opacity > 0 ? 0 : 1,
                })
              "
            >
              {{ part.opacity > 0 ? "Hide" : "Show" }}
            </button>
            <button type="button" @click="emit('resetPartOpacity', part.index)">
              Reset
            </button>
          </div>
        </li>
      </ol>

      <ol v-else-if="activeTab === 'parameters'" class="debug-list">
        <li
          v-for="parameter in filteredParameters"
          :key="parameter.index"
          :class="{ 'is-overridden': parameter.overridden }"
        >
          <div class="debug-list__meta">
            <strong>#{{ parameter.index }} {{ parameter.id }}</strong>
            <span>
              {{ formatNumber(parameter.minimum) }} ..
              {{ formatNumber(parameter.maximum) }}
            </span>
          </div>
          <div class="debug-list__controls">
            <input
              type="range"
              :min="parameter.minimum"
              :max="parameter.maximum"
              :step="(parameter.maximum - parameter.minimum) / 100 || 0.01"
              :value="parameter.value"
              @input="
                emit('setParameterValue', {
                  index: parameter.index,
                  value: readNumericInput($event),
                })
              "
            />
            <span class="debug-list__value">
              {{ formatNumber(parameter.value) }}
            </span>
            <button
              type="button"
              @click="emit('resetParameterValue', parameter.index)"
            >
              Reset
            </button>
          </div>
        </li>
      </ol>

      <ol v-else class="debug-list debug-list--drawables">
        <li v-for="drawable in filteredDrawables" :key="drawable.index">
          <div class="debug-list__meta">
            <strong>#{{ drawable.index }} {{ drawable.id }}</strong>
            <span>
              area
              {{
                drawable.bounds
                  ? formatNumber(drawable.bounds.area, 0)
                  : "n/a"
              }}
            </span>
          </div>
          <dl>
            <div>
              <dt>visible</dt>
              <dd>{{ drawable.visible ? "yes" : "no" }}</dd>
            </div>
            <div>
              <dt>opacity</dt>
              <dd>{{ formatNumber(drawable.opacity) }}</dd>
            </div>
            <div>
              <dt>order</dt>
              <dd>{{ drawable.renderOrder }}</dd>
            </div>
            <div>
              <dt>vertices</dt>
              <dd>{{ drawable.vertexCount }}</dd>
            </div>
          </dl>
        </li>
      </ol>
    </div>
  </aside>
</template>
