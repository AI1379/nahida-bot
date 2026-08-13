<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { motionDatasetKinds } from "@/domain/motionTelemetry";
import {
  clearMotionDataset,
  exportMotionDataset,
  readMotionDataset,
} from "@/services/motionDatasetStorage";
import {
  auditMotionDataset,
  type MotionDatasetAuditReport,
} from "@/services/motionDatasetAudit";

const props = defineProps<{
  enabled: boolean;
}>();

const emit = defineEmits<{
  updateEnabled: [enabled: boolean];
}>();

const counts = ref<Record<(typeof motionDatasetKinds)[number], number>>({
  decisions: 0,
  executions: 0,
  preferences: 0,
  invalid: 0,
});
const status = ref("");
const busy = ref(false);
const audit = ref<MotionDatasetAuditReport>(auditMotionDataset({}));

const totalCount = computed(() =>
  Object.values(counts.value).reduce((total, count) => total + count, 0),
);

async function refreshCounts(): Promise<void> {
  const entries = await Promise.all(
    motionDatasetKinds.map(async (kind) => [
      kind,
      await readMotionDataset(kind),
    ] as const),
  );
  const records = Object.fromEntries(entries);
  audit.value = auditMotionDataset(records);
  counts.value = audit.value.counts;
}

function criterionValue(
  current: number,
  unit: "count" | "ratio",
): string {
  return unit === "ratio" ? `${Math.round(current * 100)}%` : String(current);
}

async function exportDataset(): Promise<void> {
  busy.value = true;
  status.value = "";
  try {
    const dataset = await exportMotionDataset();
    const date = new Date().toISOString().slice(0, 10);
    for (const kind of motionDatasetKinds) {
      if (!dataset[kind]) continue;
      const blob = new Blob([`${dataset[kind]}\n`], {
        type: "application/x-ndjson",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${kind}-${date}.jsonl`;
      link.click();
      URL.revokeObjectURL(url);
    }
    status.value = "Dataset export prepared.";
  } catch (error) {
    status.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

async function clearDataset(): Promise<void> {
  if (!window.confirm("Clear all local motion training records?")) return;
  busy.value = true;
  status.value = "";
  try {
    await clearMotionDataset();
    await refreshCounts();
    status.value = "Local motion dataset cleared.";
  } catch (error) {
    status.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

onMounted(() => void refreshCounts());
</script>

<template>
  <section class="panel motion-data" aria-label="Motion training data">
    <header class="panel__header">
      <h2>Motion Data</h2>
      <span>{{ totalCount }} local records</span>
    </header>

    <div class="motion-data__body">
      <label class="motion-data__toggle">
        <input
          type="checkbox"
          :checked="props.enabled"
          @change="emit('updateEnabled', ($event.target as HTMLInputElement).checked)"
        />
        <span>Collect local motion training data</span>
      </label>
      <p>
        Use the pet normally, rate the latest motion in Runtime, or replay a
        recent real motion in Workbench. Records stay on this device until you
        explicitly export them; preview-only motions are excluded.
      </p>
      <dl>
        <div v-for="kind in motionDatasetKinds" :key="kind">
          <dt>{{ kind }}</dt>
          <dd>{{ counts[kind] }}</dd>
        </div>
      </dl>
      <div class="motion-data__readiness">
        <strong>
          Training readiness: {{ audit.readyForTraining ? "ready" : "collecting" }}
        </strong>
        <ul>
          <li
            v-for="criterion in audit.criteria"
            :key="criterion.id"
            :class="{ 'is-passed': criterion.passed }"
          >
            <span>{{ criterion.label }}</span>
            <span>
              {{ criterionValue(criterion.current, criterion.unit) }} /
              {{ criterionValue(criterion.target, criterion.unit) }}
            </span>
          </li>
        </ul>
        <p v-if="audit.issues.length">
          {{ audit.issues.length }} data integrity issue(s) found in the local set.
        </p>
      </div>
      <div class="motion-data__actions">
        <button type="button" :disabled="busy" @click="refreshCounts">
          Refresh
        </button>
        <button type="button" :disabled="busy || !totalCount" @click="exportDataset">
          Export
        </button>
        <button type="button" :disabled="busy || !totalCount" @click="clearDataset">
          Clear
        </button>
      </div>
      <p v-if="status" class="motion-data__status" aria-live="polite">
        {{ status }}
      </p>
    </div>
  </section>
</template>
