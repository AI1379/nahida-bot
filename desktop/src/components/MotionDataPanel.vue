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
const kindLabels: Record<(typeof motionDatasetKinds)[number], string> = {
  decisions: "动作决策",
  executions: "动作执行",
  preferences: "用户反馈",
  invalid: "无效记录",
};

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
    status.value = "动作数据导出已准备完成。";
  } catch (error) {
    status.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

async function clearDataset(): Promise<void> {
  if (!window.confirm("清除全部本地动作训练记录？此操作无法撤销。")) return;
  busy.value = true;
  status.value = "";
  try {
    await clearMotionDataset();
    await refreshCounts();
    status.value = "本地动作数据已清除。";
  } catch (error) {
    status.value = error instanceof Error ? error.message : String(error);
  } finally {
    busy.value = false;
  }
}

onMounted(() => void refreshCounts());
</script>

<template>
  <section class="panel motion-data" aria-label="动作训练数据">
    <header class="panel__header">
      <h2>动作数据</h2>
      <span>{{ totalCount }} 条本地记录</span>
    </header>

    <div class="motion-data__body">
      <label class="motion-data__toggle">
        <input
          type="checkbox"
          :checked="props.enabled"
          @change="emit('updateEnabled', ($event.target as HTMLInputElement).checked)"
        />
        <span>收集本地动作训练数据</span>
      </label>
      <p>
        正常使用桌宠、评价最近动作或在开发工具中回放真实动作即可积累记录。
        数据会留在本机，只有手动导出时才会离开；仅预览的动作不会计入。
      </p>
      <dl>
        <div v-for="kind in motionDatasetKinds" :key="kind">
          <dt>{{ kindLabels[kind] }}</dt>
          <dd>{{ counts[kind] }}</dd>
        </div>
      </dl>
      <div class="motion-data__readiness">
        <strong>
          训练准备度：{{ audit.readyForTraining ? "已就绪" : "收集中" }}
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
          本地数据中发现 {{ audit.issues.length }} 个完整性问题。
        </p>
      </div>
      <div class="motion-data__actions">
        <button type="button" :disabled="busy" @click="refreshCounts">
          刷新
        </button>
        <button type="button" :disabled="busy || !totalCount" @click="exportDataset">
          导出
        </button>
        <button type="button" :disabled="busy || !totalCount" @click="clearDataset">
          清除
        </button>
      </div>
      <p v-if="status" class="motion-data__status" aria-live="polite">
        {{ status }}
      </p>
    </div>
  </section>
</template>
