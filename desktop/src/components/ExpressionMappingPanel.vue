<script setup lang="ts">
import { computed } from "vue";

import type {
  Live2DExpressionOption,
  Live2DModelManifest,
} from "@/domain/live2d";

const props = defineProps<{
  model: Live2DModelManifest;
  expressions: Live2DExpressionOption[];
}>();

const emit = defineEmits<{
  addMapping: [];
  removeMapping: [keyword: string];
  updateMapping: [keyword: string, nextKeyword: string, expressionName: string];
  preview: [keyword: string];
}>();

const builtInKeywordOrder = [
  "neutral",
  "happy",
  "thinking",
  "worried",
  "error",
  "offline",
];

const expressionOptions = computed(() => {
  const options = new Map<string, Live2DExpressionOption>();
  for (const expression of props.expressions) {
    options.set(expression.name, expression);
  }
  for (const expressionNames of Object.values(props.model.emotionMap)) {
    const expressionName = expressionNames?.[0];
    if (expressionName && !options.has(expressionName)) {
      options.set(expressionName, {
        index: -1,
        name: expressionName,
        file: "saved mapping",
      });
    }
  }
  return Array.from(options.values());
});

const mappingRows = computed(() =>
  Object.entries(props.model.emotionMap)
    .map(([keyword, expressions]) => ({
      keyword,
      expressionName: expressions[0] ?? "",
    }))
    .sort((left, right) => {
      const leftIndex = builtInKeywordOrder.indexOf(left.keyword);
      const rightIndex = builtInKeywordOrder.indexOf(right.keyword);
      if (leftIndex >= 0 || rightIndex >= 0) {
        return (
          (leftIndex >= 0 ? leftIndex : Number.MAX_SAFE_INTEGER) -
          (rightIndex >= 0 ? rightIndex : Number.MAX_SAFE_INTEGER)
        );
      }
      return left.keyword.localeCompare(right.keyword);
    }),
);

function optionLabel(expression: Live2DExpressionOption): string {
  return expression.file && expression.file !== expression.name
    ? `${expression.name} (${expression.file})`
    : expression.name;
}

function updateKeyword(
  keyword: string,
  expressionName: string,
  event: Event,
) {
  const target = event.target as HTMLInputElement;
  emit("updateMapping", keyword, target.value, expressionName);
}

function updateExpression(keyword: string, event: Event) {
  const target = event.target as HTMLSelectElement;
  emit("updateMapping", keyword, keyword, target.value);
}
</script>

<template>
  <section class="panel expression-map" aria-label="Expression mapping">
    <header class="panel__header">
      <h2>Expression Map</h2>
      <button type="button" @click="emit('addMapping')">Add</button>
    </header>

    <div class="expression-map__summary">
      <span>{{ mappingRows.length }} keywords</span>
      <span>{{ expressionOptions.length }} expressions</span>
    </div>

    <div v-if="!mappingRows.length" class="expression-map__empty">
      No expression keyword mappings yet.
    </div>

    <ul class="expression-map__list">
      <li v-for="row in mappingRows" :key="row.keyword">
        <div class="expression-map__label">
          <label :for="`expression-keyword-${row.keyword}`">keyword</label>
          <input
            :id="`expression-keyword-${row.keyword}`"
            :value="row.keyword"
            spellcheck="false"
            @change="updateKeyword(row.keyword, row.expressionName, $event)"
          />
        </div>
        <div class="expression-map__controls">
          <select
            :id="`expression-map-${row.keyword}`"
            :value="row.expressionName"
            @change="updateExpression(row.keyword, $event)"
          >
            <option value="">None</option>
            <option
              v-for="expression in expressionOptions"
              :key="`${expression.index}-${expression.name}-${expression.file}`"
              :value="expression.name"
            >
              {{ optionLabel(expression) }}
            </option>
          </select>
          <button
            type="button"
            :disabled="!row.expressionName"
            @click="emit('preview', row.keyword)"
          >
            Test
          </button>
          <button type="button" @click="emit('removeMapping', row.keyword)">
            Remove
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>
