<script setup lang="ts">
import { ref, computed, watch } from "vue";
import {
  DialogRoot,
  DialogPortal,
  DialogOverlay,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "reka-ui";
import Button from "@/components/ui/Button.vue";
import Input from "@/components/ui/Input.vue";
import Textarea from "@/components/ui/Textarea.vue";
import Spinner from "@/components/ui/Spinner.vue";
import type { CronJob, CreateCronRequest, UpdateCronRequest } from "@/api/schemas";

const props = defineProps<{
  open: boolean;
  job?: CronJob | null;
}>();

const emit = defineEmits<{
  "update:open": [value: boolean];
}>();

const isCreate = computed(() => !props.job);

const target = ref("");
const prompt = ref("");
const mode = ref<"once" | "interval" | "cron">("once");
const fireAt = ref("");
const intervalSeconds = ref("");
const cronExpression = ref("");
const maxRuns = ref("");
const sessionMode = ref<"main" | "isolated" | "named">("main");
const sessionName = ref("");

const loading = defineModel<boolean>("loading", { default: false });

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    if (props.job) {
      prompt.value = props.job.prompt;
      mode.value = props.job.mode as "once" | "interval" | "cron";
      fireAt.value = props.job.fire_at ?? "";
      intervalSeconds.value = props.job.interval_seconds != null ? String(props.job.interval_seconds) : "";
      cronExpression.value = props.job.cron_expression ?? "";
      maxRuns.value = props.job.max_runs != null ? String(props.job.max_runs) : "";
      sessionMode.value = props.job.session_mode as "main" | "isolated" | "named";
      sessionName.value = props.job.session_name ?? "";
      target.value = "";
    } else {
      target.value = "";
      prompt.value = "";
      mode.value = "once";
      fireAt.value = "";
      intervalSeconds.value = "";
      cronExpression.value = "";
      maxRuns.value = "";
      sessionMode.value = "main";
      sessionName.value = "";
    }
  },
);

function buildCreatePayload(): CreateCronRequest {
  return {
    target: target.value,
    prompt: prompt.value,
    mode: mode.value,
    fire_at: fireAt.value || null,
    interval_seconds: intervalSeconds.value ? Number(intervalSeconds.value) : null,
    cron_expression: cronExpression.value || null,
    max_runs: maxRuns.value ? Number(maxRuns.value) : null,
    session_mode: sessionMode.value,
    session_name: sessionMode.value === "named" ? sessionName.value : null,
  };
}

function buildUpdatePayload(): UpdateCronRequest {
  return {
    prompt: prompt.value || null,
    mode: mode.value,
    fire_at: fireAt.value || null,
    interval_seconds: intervalSeconds.value ? Number(intervalSeconds.value) : null,
    cron_expression: cronExpression.value || null,
    max_runs: maxRuns.value ? Number(maxRuns.value) : null,
  };
}

function submit() {
  emit("update:open", false);
}

defineExpose({ buildCreatePayload, buildUpdatePayload });
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="dialog-overlay" />
      <DialogContent class="dialog-content">
        <div class="dialog-header">
          <DialogTitle class="dialog-title">
            {{ isCreate ? "Create CRON Job" : "Edit CRON Job" }}
          </DialogTitle>
          <DialogClose as-child>
            <button class="dialog-close" aria-label="Close">&times;</button>
          </DialogClose>
        </div>
        <div class="dialog-body">
          <DialogDescription class="sr-only">
            {{ isCreate ? "Create a new scheduled task." : "Edit an existing scheduled task." }}
          </DialogDescription>

          <div v-if="isCreate" class="field">
            <label class="field-label">Target <span class="required">*</span></label>
            <Input v-model="target" placeholder="platform:type:id (e.g. milky:group:20001)" />
          </div>

          <div class="field">
            <label class="field-label">Prompt <span class="required">*</span></label>
            <Textarea v-model="prompt" placeholder="What should the bot do?" :rows="3" />
          </div>

          <div class="field-row">
            <div class="field">
              <label class="field-label">Mode</label>
              <select v-model="mode" class="field-select">
                <option value="once">Once</option>
                <option value="interval">Interval</option>
                <option value="cron">Cron</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">Session Mode</label>
              <select v-model="sessionMode" class="field-select">
                <option value="main">Main</option>
                <option value="isolated">Isolated</option>
                <option value="named">Named</option>
              </select>
            </div>
          </div>

          <div v-if="mode === 'once'" class="field">
            <label class="field-label">Fire At</label>
            <Input v-model="fireAt" type="datetime-local" />
          </div>

          <div v-if="mode === 'interval'" class="field">
            <label class="field-label">Interval (seconds)</label>
            <Input v-model="intervalSeconds" type="number" placeholder="3600" />
          </div>

          <div v-if="mode === 'cron'" class="field">
            <label class="field-label">Cron Expression</label>
            <Input v-model="cronExpression" placeholder="0 9 * * *" />
          </div>

          <div class="field">
            <label class="field-label">Max Runs</label>
            <Input v-model="maxRuns" type="number" placeholder="Unlimited" />
          </div>

          <div v-if="sessionMode === 'named'" class="field">
            <label class="field-label">Session Name <span class="required">*</span></label>
            <Input v-model="sessionName" placeholder="e.g. daily-summary" />
          </div>
        </div>
        <div class="dialog-footer">
          <DialogClose as-child>
            <Button variant="outline" size="sm" :disabled="loading">Cancel</Button>
          </DialogClose>
          <Button size="sm" :disabled="loading || !prompt.trim()" @click="submit">
            <Spinner v-if="loading" size="sm" />
            {{ isCreate ? "Create" : "Save" }}
          </Button>
        </div>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: oklch(0 0 0 / 0.4);
  animation: overlay-in 0.15s ease-out;
}

@keyframes overlay-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.dialog-content {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 51;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  width: min(520px, 90vw);
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px oklch(0 0 0 / 0.2);
  animation: content-in 0.15s ease-out;
}

@keyframes content-in {
  from { opacity: 0; transform: translate(-50%, -48%); }
  to { opacity: 1; transform: translate(-50%, -50%); }
}

.dialog-content:focus {
  outline: none;
}

.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.dialog-title {
  font-size: 0.875rem;
  font-weight: 600;
}

.dialog-close {
  background: none;
  border: none;
  font-size: 1.25rem;
  cursor: pointer;
  color: var(--color-muted-foreground);
  padding: 0 0.25rem;
  line-height: 1;
}

.dialog-close:hover {
  color: var(--color-foreground);
}

.dialog-body {
  padding: 1rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--color-border);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.field-label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-muted-foreground);
}

.required {
  color: var(--color-destructive);
}

.field-select {
  width: 100%;
  height: 32px;
  padding: 0 0.5rem;
  font-size: 0.8125rem;
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-foreground);
  cursor: pointer;
}

.field-select:focus {
  outline: none;
  border-color: var(--color-ring);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-ring) 25%, transparent);
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
