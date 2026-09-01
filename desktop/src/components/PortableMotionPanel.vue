<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { NormalizedMotionClip } from "@/domain/normalizedPose";
import type { PortableMotionTargetModel } from "@/domain/portableMotion";
import {
  importPortableMotionForWorkbench,
  portableMotionWorkbenchMaximumFileSize,
  portableMotionSparklinePoints,
  type PortableMotionWorkbenchResult,
} from "@/services/portableMotionWorkbench";

const props = defineProps<{
  target: PortableMotionTargetModel | null;
  previewStatus: string;
}>();

const emit = defineEmits<{
  preview: [clip: NormalizedMotionClip];
  resetPreviewStatus: [];
}>();

interface SelectedMotionSource {
  fileName: string;
  text: string;
  size: number;
}

const source = ref<SelectedMotionSource | null>(null);
const result = ref<PortableMotionWorkbenchResult | null>(null);
const errorMessage = ref("");
const loading = ref(false);
let importGeneration = 0;

const coverageLabel = computed(() =>
  result.value ? `${Math.round(result.value.poseCoverage * 100)}%` : "—",
);

function formatNumber(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : "n/a";
}

function formatDuration(durationMs: number): string {
  return `${(durationMs / 1000).toFixed(2)}s`;
}

function analyzeSelectedSource(): void {
  errorMessage.value = "";
  result.value = null;
  if (!source.value || !props.target) return;
  try {
    result.value = importPortableMotionForWorkbench(source.value, props.target);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error);
  }
}

async function selectFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  emit("resetPreviewStatus");
  if (file.size > portableMotionWorkbenchMaximumFileSize) {
    errorMessage.value = "motion file exceeds the 16 MiB Workbench limit";
    return;
  }
  const generation = ++importGeneration;
  loading.value = true;
  errorMessage.value = "";
  try {
    const text = await file.text();
    if (generation !== importGeneration) return;
    source.value = { fileName: file.name, text, size: file.size };
    analyzeSelectedSource();
  } catch (error) {
    if (generation !== importGeneration) return;
    errorMessage.value = error instanceof Error ? error.message : String(error);
  } finally {
    if (generation === importGeneration) loading.value = false;
  }
}

function clearImport(): void {
  emit("resetPreviewStatus");
  importGeneration += 1;
  source.value = null;
  result.value = null;
  errorMessage.value = "";
  loading.value = false;
}

function preview(): void {
  if (result.value?.clip) emit("preview", result.value.clip);
}

watch(
  () => props.target,
  () => {
    emit("resetPreviewStatus");
    analyzeSelectedSource();
  },
);
</script>

<template>
  <section class="panel portable-motion" aria-label="Portable motion import">
    <header class="panel__header">
      <h2>Portable Motion</h2>
      <span>{{ props.target?.modelName ?? "model loading" }}</span>
    </header>

    <div class="portable-motion__body">
      <div class="portable-motion__toolbar">
        <label class="portable-motion__file">
          <span>{{ source?.fileName ?? "Choose .mtn or .motion3.json" }}</span>
          <input
            type="file"
            accept=".mtn,.motion3.json,.json,application/json,text/plain"
            :disabled="!props.target || loading"
            @change="selectFile"
          />
        </label>
        <button type="button" :disabled="!result?.clip" @click="preview">
          Preview
        </button>
        <button type="button" :disabled="!source" @click="clearImport">
          Clear
        </button>
      </div>

      <p v-if="!props.target" class="portable-motion__note">
        Waiting for the active Live2D model and its parameter ranges.
      </p>
      <p v-else-if="loading" class="portable-motion__note">Reading motion file…</p>
      <p v-if="errorMessage" class="portable-motion__error">{{ errorMessage }}</p>
      <p v-if="result && props.previewStatus" class="portable-motion__status">
        {{ props.previewStatus }}
      </p>

      <template v-if="result">
        <dl class="portable-motion__summary">
          <div><dt>Format</dt><dd>{{ result.audit.format }}</dd></div>
          <div><dt>Duration</dt><dd>{{ formatDuration(result.asset.durationMs) }}</dd></div>
          <div><dt>Frames</dt><dd>{{ result.asset.frames.length }}</dd></div>
          <div><dt>Source</dt><dd>{{ result.audit.sourceItemCount }}</dd></div>
          <div><dt>Portable</dt><dd>{{ result.audit.importedItemCount }}</dd></div>
          <div><dt>Target</dt><dd>{{ result.supportedChannels.length }}</dd></div>
          <div><dt>Coverage</dt><dd>{{ coverageLabel }}</dd></div>
          <div>
            <dt>Status</dt>
            <dd :data-status="result.compatibilityStatus">
              {{ result.compatibilityStatus }}
            </dd>
          </div>
        </dl>

        <p v-if="result.audit.format === 'motion3'" class="portable-motion__note">
          motion3 ranges are interpreted against the active target model.
        </p>

        <details class="portable-motion__curves" open>
          <summary>Curves ({{ result.curves.length }})</summary>
          <ol>
            <li
              v-for="curve in result.curves"
              :key="curve.channel"
              :class="{ 'is-missing': !curve.targetParameterId }"
            >
              <div class="portable-motion__curve-label">
                <strong>{{ curve.channel }}</strong>
                <span>{{ curve.targetParameterId ?? "not mapped" }}</span>
              </div>
              <svg
                viewBox="0 0 180 42"
                preserveAspectRatio="none"
                role="img"
                :aria-label="`${curve.channel} curve`"
              >
                <line x1="0" y1="21" x2="180" y2="21" />
                <polyline :points="portableMotionSparklinePoints(curve.samples)" />
              </svg>
              <div class="portable-motion__curve-values">
                <span>{{ formatNumber(curve.minimum) }} .. {{ formatNumber(curve.maximum) }}</span>
                <span>{{ formatNumber(curve.start) }} → {{ formatNumber(curve.end) }}</span>
              </div>
            </li>
          </ol>
        </details>

        <details
          v-if="result.audit.skipped.length || result.missingChannels.length"
          class="portable-motion__losses"
        >
          <summary>
            Losses ({{ result.audit.skipped.length + result.missingChannels.length }})
          </summary>
          <ul>
            <li v-for="channel in result.missingChannels" :key="`missing:${channel}`">
              <code>{{ channel }}</code> — target channel missing
            </li>
            <li
              v-for="(loss, index) in result.audit.skipped"
              :key="`skip:${loss.id}:${index}`"
            >
              <code>{{ loss.id }}</code> — {{ loss.reason }}
            </li>
          </ul>
        </details>

        <details
          v-if="result.audit.clamped.length || result.audit.assumedRangeParameterIds.length"
          class="portable-motion__losses"
        >
          <summary>Range assumptions</summary>
          <ul>
            <li v-for="item in result.audit.clamped" :key="`clamp:${item.id}`">
              <code>{{ item.id }}</code> — {{ item.sampleCount }} samples clamped
            </li>
            <li
              v-for="id in result.audit.assumedRangeParameterIds"
              :key="`assumed:${id}`"
            >
              <code>{{ id }}</code> — Live2D standard range assumed
            </li>
          </ul>
        </details>
      </template>
    </div>
  </section>
</template>
