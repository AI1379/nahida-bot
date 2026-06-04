<script setup lang="ts">
import { computed, ref, toRaw, watch } from "vue";
import {
  useConfigDocument,
  useConfigPatchSave,
  useConfigSchema,
  usePluginList,
} from "@/api/queries";
import Badge from "@/components/ui/Badge.vue";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Spinner from "@/components/ui/Spinner.vue";
import Tabs from "@/components/ui/Tabs.vue";
import Textarea from "@/components/ui/Textarea.vue";
import { schemaEntriesToFields } from "@/features/plugins/jsonSchemaForm";
import type { SchemaField } from "@/features/plugins/jsonSchemaForm";
import ConfirmDialog from "@/components/ui/ConfirmDialog.vue";
import { api } from "@/api/client";
import type { ConfigPatchChange, ConfigValidateResponse } from "@/api/schemas";

type ConfigMap = Record<string, unknown>;
type FieldKind =
  | "text"
  | "number"
  | "boolean"
  | "textarea"
  | "select"
  | "list"
  | "secret"
  | "tri-state";
type BadgeVariant = "default" | "success" | "warning" | "destructive" | "secondary" | "outline";
type CapabilityValueType = "boolean" | "number" | "string" | "list" | "null";

interface CapabilityEntry {
  key: string;
  value: unknown;
  type: CapabilityValueType;
}

interface FieldDef {
  path: string;
  label: string;
  kind: FieldKind;
  options?: string[];
  wide?: boolean;
}

interface FieldGroup {
  id: string;
  title: string;
  description: string;
  fields: FieldDef[];
}

interface SectionDef {
  id: string;
  label: string;
}

const mode = ref("form");
const modeTabs = [
  { id: "form", label: "Form" },
  { id: "yaml", label: "YAML" },
  { id: "schema", label: "Schema" },
];

const sections: SectionDef[] = [
  { id: "general", label: "General" },
  { id: "providers", label: "Providers" },
  { id: "channels", label: "Channels" },
  { id: "multimodal", label: "Multimodal" },
  { id: "agent", label: "Agent" },
  { id: "router", label: "Router" },
  { id: "context", label: "Context" },
  { id: "memory", label: "Memory" },
  { id: "scheduler", label: "Scheduler" },
];

const activeSection = ref("general");
const draft = ref<ConfigMap>({});
const original = ref<ConfigMap>({});
const baseChecksum = ref("");
const validation = ref<ConfigValidateResponse | null>(null);
const validating = ref(false);
const showSaveDialog = ref(false);
const pendingChanges = ref<ConfigPatchChange[]>([]);
const newProviderId = ref("");

const {
  data: configDoc,
  isLoading: configLoading,
  error: configError,
} = useConfigDocument();
const { data: schemaData, isLoading: schemaLoading } = useConfigSchema();
const { data: pluginData } = usePluginList();
const saveMutation = useConfigPatchSave();

// --- Plugins section state ---
const pluginsExpanded = ref(true);
const activePluginId = ref("");

const pluginList = computed(() => pluginData.value?.plugins ?? []);

const activePluginFields = computed<SchemaField[]>(() => {
  if (!activePluginId.value || !schemaData.value) return [];
  return schemaEntriesToFields(schemaData.value.entries, activePluginId.value);
});

function selectPlugin(pluginId: string) {
  activePluginId.value = pluginId;
  activeSection.value = `plugin:${pluginId}`;
}

function isPluginSection(sectionId: string): boolean {
  return sectionId.startsWith("plugin:");
}

function pluginIdFromSection(sectionId: string): string {
  return sectionId.startsWith("plugin:") ? sectionId.slice(7) : "";
}

const providerTypeOptions = [
  "openai-compatible",
  "deepseek",
  "glm",
  "groq",
  "anthropic",
  "minimax",
  "openai-responses",
];

const groupTriggerOptions = ["always", "mention", "command"];
const replyOverrideOptions = ["inherit", "true", "false"];

const channelFieldGroups: FieldGroup[] = [
  {
    id: "telegram",
    title: "Telegram",
    description: "Telegram Bot long polling, group trigger policy, and media download settings.",
    fields: [
      { path: "telegram.bot_token", label: "Bot token", kind: "secret" },
      { path: "telegram.proxy", label: "Proxy", kind: "text" },
      { path: "telegram.polling_timeout", label: "Polling timeout", kind: "number" },
      {
        path: "telegram.polling_max_backoff",
        label: "Max polling backoff",
        kind: "number",
      },
      { path: "telegram.allowed_chats", label: "Allowed chats", kind: "list", wide: true },
      {
        path: "telegram.group_trigger_mode",
        label: "Group trigger",
        kind: "select",
        options: groupTriggerOptions,
      },
      {
        path: "telegram.group_context_capture",
        label: "Capture group context",
        kind: "boolean",
      },
      {
        path: "telegram.reply_to_inbound",
        label: "Reply to inbound",
        kind: "tri-state",
        options: replyOverrideOptions,
      },
      { path: "telegram.send_retry_attempts", label: "Send retries", kind: "number" },
      { path: "telegram.media_download_dir", label: "Media directory", kind: "text" },
    ],
  },
  {
    id: "onebot",
    title: "OneBot",
    description: "OneBot v11 forward WebSocket, outbound API, access control, and media settings.",
    fields: [
      {
        path: "onebot.protocol_version",
        label: "Protocol",
        kind: "select",
        options: ["v11", "auto", "v12"],
      },
      { path: "onebot.ws_url", label: "WebSocket URL", kind: "text" },
      { path: "onebot.ws_access_token", label: "WebSocket token", kind: "secret" },
      { path: "onebot.impl_base_url", label: "HTTP API base", kind: "text" },
      { path: "onebot.impl_access_token", label: "HTTP API token", kind: "secret" },
      { path: "onebot.webhook_enabled", label: "Webhook enabled", kind: "boolean" },
      { path: "onebot.webhook_host", label: "Webhook host", kind: "text" },
      { path: "onebot.webhook_port", label: "Webhook port", kind: "number" },
      { path: "onebot.webhook_path", label: "Webhook path", kind: "text" },
      { path: "onebot.webhook_secret", label: "Webhook secret", kind: "secret" },
      { path: "onebot.command_prefix", label: "Command prefix", kind: "text" },
      {
        path: "onebot.group_trigger_mode",
        label: "Group trigger",
        kind: "select",
        options: groupTriggerOptions,
      },
      {
        path: "onebot.group_context_capture",
        label: "Capture group context",
        kind: "boolean",
      },
      {
        path: "onebot.reply_to_inbound",
        label: "Reply to inbound",
        kind: "tri-state",
        options: replyOverrideOptions,
      },
      { path: "onebot.allowed_friends", label: "Allowed friends", kind: "list", wide: true },
      { path: "onebot.allowed_groups", label: "Allowed groups", kind: "list", wide: true },
      {
        path: "onebot.reconnect_initial_delay",
        label: "Reconnect initial delay",
        kind: "number",
      },
      {
        path: "onebot.reconnect_max_delay",
        label: "Reconnect max delay",
        kind: "number",
      },
      { path: "onebot.max_text_length", label: "Max text length", kind: "number" },
      { path: "onebot.split_long_text", label: "Split long text", kind: "boolean" },
      { path: "onebot.media_download_dir", label: "Media directory", kind: "text" },
      {
        path: "onebot.enable_media_download_tool",
        label: "Media download tool",
        kind: "boolean",
      },
      {
        path: "onebot.cache_media_on_receive",
        label: "Cache inbound media",
        kind: "boolean",
      },
    ],
  },
  {
    id: "milky",
    title: "Milky",
    description: "Milky HTTP/WebSocket endpoints, access lists, retries, and rich message settings.",
    fields: [
      { path: "milky.base_url", label: "Base URL", kind: "text" },
      { path: "milky.access_token", label: "Access token", kind: "secret" },
      { path: "milky.api_prefix", label: "API prefix", kind: "text" },
      { path: "milky.event_path", label: "Event path", kind: "text" },
      { path: "milky.ws_url", label: "WebSocket override", kind: "text" },
      { path: "milky.command_prefix", label: "Command prefix", kind: "text" },
      {
        path: "milky.group_trigger_mode",
        label: "Group trigger",
        kind: "select",
        options: groupTriggerOptions,
      },
      {
        path: "milky.group_context_capture",
        label: "Capture group context",
        kind: "boolean",
      },
      {
        path: "milky.reply_to_inbound",
        label: "Reply to inbound",
        kind: "tri-state",
        options: replyOverrideOptions,
      },
      { path: "milky.allowed_friends", label: "Allowed friends", kind: "list", wide: true },
      { path: "milky.allowed_groups", label: "Allowed groups", kind: "list", wide: true },
      { path: "milky.connect_timeout", label: "Connect timeout", kind: "number" },
      { path: "milky.heartbeat_timeout", label: "Heartbeat timeout", kind: "number" },
      {
        path: "milky.reconnect_initial_delay",
        label: "Reconnect initial delay",
        kind: "number",
      },
      {
        path: "milky.reconnect_max_delay",
        label: "Reconnect max delay",
        kind: "number",
      },
      { path: "milky.send_retry_attempts", label: "Send retries", kind: "number" },
      { path: "milky.send_retry_backoff", label: "Send retry backoff", kind: "number" },
      { path: "milky.max_text_length", label: "Max text length", kind: "number" },
      { path: "milky.media_download_dir", label: "Media directory", kind: "text" },
      {
        path: "milky.enable_media_download_tool",
        label: "Media download tool",
        kind: "boolean",
      },
      {
        path: "milky.resource_url_ttl_hint",
        label: "Resource URL TTL",
        kind: "number",
      },
      {
        path: "milky.cache_media_on_receive",
        label: "Cache inbound media",
        kind: "boolean",
      },
      { path: "milky.max_forward_depth", label: "Forward depth", kind: "number" },
      { path: "milky.max_forward_messages", label: "Forward messages", kind: "number" },
      {
        path: "milky.forward_render_max_chars",
        label: "Forward render chars",
        kind: "number",
      },
      { path: "milky.scene_cache_size", label: "Scene cache size", kind: "number" },
    ],
  },
];

const multimodalFields: FieldDef[] = [
  {
    path: "multimodal.image_fallback_mode",
    label: "Image fallback",
    kind: "select",
    options: ["auto", "tool", "off"],
  },
  {
    path: "multimodal.media_context_policy",
    label: "Media context",
    kind: "select",
    options: ["cache_aware", "native_recent", "description_only"],
  },
  { path: "multimodal.image_fallback_model", label: "Fallback model", kind: "text" },
  { path: "multimodal.max_images_per_turn", label: "Max images per turn", kind: "number" },
  { path: "multimodal.max_image_bytes", label: "Max image bytes", kind: "number" },
  { path: "multimodal.media_cache_ttl_seconds", label: "Media cache TTL", kind: "number" },
];

const agentFields: FieldDef[] = [
  { path: "agent.max_steps", label: "Max steps", kind: "number" },
  { path: "agent.provider_timeout_seconds", label: "Provider timeout", kind: "number" },
  { path: "agent.retry_attempts", label: "Provider retries", kind: "number" },
  { path: "agent.retry_backoff_seconds", label: "Provider retry backoff", kind: "number" },
  { path: "agent.tool_timeout_seconds", label: "Tool timeout", kind: "number" },
  { path: "agent.tool_retry_attempts", label: "Tool retries", kind: "number" },
  { path: "agent.tool_retry_backoff_seconds", label: "Tool retry backoff", kind: "number" },
  { path: "agent.max_tool_log_chars", label: "Max tool log chars", kind: "number" },
  { path: "agent.tool_use_system_prompt", label: "Tool use system prompt", kind: "textarea" },
  { path: "agent.provider_error_template", label: "Provider error template", kind: "textarea" },
];

const routerFields: FieldDef[] = [
  { path: "router.max_history_turns", label: "Max history turns", kind: "number" },
  { path: "router.agent_enabled", label: "Agent enabled", kind: "boolean" },
  { path: "router.command_timeout_seconds", label: "Command timeout", kind: "number" },
  { path: "router.command_timeout_message", label: "Command timeout message", kind: "text" },
  { path: "router.reply_to_inbound", label: "Reply to inbound", kind: "boolean" },
  { path: "router.show_reasoning", label: "Show reasoning", kind: "boolean" },
  { path: "router.reasoning_max_chars", label: "Reasoning max chars", kind: "number" },
  { path: "router.enable_silent_reply", label: "Silent replies", kind: "boolean" },
  { path: "router.group_context.enabled", label: "Group context", kind: "boolean" },
  { path: "router.group_context.max_messages", label: "Group context messages", kind: "number" },
  { path: "router.group_context.ttl_seconds", label: "Group context TTL", kind: "number" },
  { path: "router.group_context.max_chars", label: "Group context chars", kind: "number" },
];

const contextFields: FieldDef[] = [
  { path: "context.max_tokens", label: "Max tokens", kind: "number" },
  { path: "context.reserved_tokens", label: "Reserved tokens", kind: "number" },
  { path: "context.max_chars", label: "Max chars", kind: "number" },
  { path: "context.reserved_chars", label: "Reserved chars", kind: "number" },
  { path: "context.summary_max_chars", label: "Summary max chars", kind: "number" },
  {
    path: "context.reasoning_policy",
    label: "Reasoning policy",
    kind: "select",
    options: ["strip", "append", "budget"],
  },
  { path: "context.max_reasoning_tokens", label: "Reasoning tokens", kind: "number" },
];

const memoryFields: FieldDef[] = [
  { path: "memory.enabled", label: "Memory enabled", kind: "boolean" },
  { path: "memory.retrieval.fts_enabled", label: "FTS retrieval", kind: "boolean" },
  { path: "memory.retrieval.vector_enabled", label: "Vector retrieval", kind: "boolean" },
  { path: "memory.retrieval.hybrid_enabled", label: "Hybrid retrieval", kind: "boolean" },
  {
    path: "memory.retrieval.vector_backend",
    label: "Vector backend",
    kind: "select",
    options: ["json", "sqlite-vec", "none"],
  },
  { path: "memory.retrieval.max_injected_items", label: "Injected items", kind: "number" },
  { path: "memory.retrieval.max_injected_chars", label: "Injected chars", kind: "number" },
  { path: "memory.embedding.enabled", label: "Embedding enabled", kind: "boolean" },
  { path: "memory.embedding.model", label: "Embedding model", kind: "text" },
  { path: "memory.embedding.dimensions", label: "Dimensions", kind: "number" },
  { path: "memory.embedding.batch_size", label: "Batch size", kind: "number" },
  {
    path: "memory.embedding.embed_after_consolidation",
    label: "Embed after consolidation",
    kind: "boolean",
  },
  {
    path: "memory.consolidation.rule_based_enabled",
    label: "Rule-based consolidation",
    kind: "boolean",
  },
];

const schedulerFields: FieldDef[] = [
  { path: "scheduler.poll_interval_seconds", label: "Poll interval", kind: "number" },
  { path: "scheduler.max_concurrent_fires", label: "Max concurrent fires", kind: "number" },
  { path: "scheduler.job_timeout_seconds", label: "Job timeout", kind: "number" },
  { path: "scheduler.min_interval_seconds", label: "Min interval", kind: "number" },
  { path: "scheduler.max_prompt_chars", label: "Max prompt chars", kind: "number" },
  { path: "scheduler.max_jobs_per_chat", label: "Max jobs per chat", kind: "number" },
  { path: "scheduler.failure_retry_seconds", label: "Failure retry", kind: "number" },
  { path: "scheduler.max_consecutive_failures", label: "Max failures", kind: "number" },
  { path: "scheduler.memory_dreaming_enabled", label: "Memory dreaming", kind: "boolean" },
  {
    path: "scheduler.memory_dreaming_interval_seconds",
    label: "Dreaming interval",
    kind: "number",
  },
  {
    path: "scheduler.memory_dreaming_initial_delay_seconds",
    label: "Dreaming initial delay",
    kind: "number",
  },
  {
    path: "scheduler.memory_dreaming_session_limit",
    label: "Dreaming session limit",
    kind: "number",
  },
  {
    path: "scheduler.memory_dreaming_recent_turn_limit",
    label: "Dreaming turn limit",
    kind: "number",
  },
  { path: "scheduler.memory_dreaming_model", label: "Dreaming model", kind: "text" },
];

const sectionCopy: Record<string, { title: string; description: string }> = {
  general: {
    title: "General",
    description: "Application identity, runtime paths, logging, and default model routing.",
  },
  channels: {
    title: "Channels",
    description: "Messaging channel plugin settings for Telegram, OneBot, and Milky.",
  },
  multimodal: {
    title: "Multimodal",
    description: "Image fallback and media context policy.",
  },
  agent: {
    title: "Agent",
    description: "Agent loop limits, retry policy, and tool prompt.",
  },
  router: {
    title: "Router",
    description: "Message routing behavior and group context injection.",
  },
  context: {
    title: "Context",
    description: "Fallback context window and reasoning budget.",
  },
  memory: {
    title: "Memory",
    description: "Long-term memory retrieval, embedding, and consolidation.",
  },
  scheduler: {
    title: "Scheduler",
    description: "Cron scheduler limits and memory dreaming schedule.",
  },
  plugins: {
    title: "Plugins",
    description: "Configuration for loaded plugins. Settings are stored as top-level keys in config.yaml.",
  },
};

const currentValueByPath = computed(() => {
  const entries = configDoc.value?.entries ?? [];
  return new Map(entries.map((entry) => [entry.path, entry.value]));
});

const redactedPaths = computed(() => new Set(configDoc.value?.redacted_paths ?? []));

const providers = computed(() => {
  const value = getValue("providers");
  return isRecord(value) ? value : {};
});

const providerIds = computed(() => Object.keys(providers.value));

const defaultProviderOptions = computed(() => ["", ...providerIds.value]);

const generalFields = computed<FieldDef[]>(() => [
  { path: "app_name", label: "App name", kind: "text" },
  { path: "debug", label: "Debug mode", kind: "boolean" },
  {
    path: "log_level",
    label: "Log level",
    kind: "select",
    options: ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"],
  },
  { path: "host", label: "Host", kind: "text" },
  { path: "port", label: "Port", kind: "number" },
  { path: "db_path", label: "Database path", kind: "text" },
  { path: "workspace_base_dir", label: "Workspace base", kind: "text" },
  { path: "plugin_paths", label: "Plugin paths", kind: "list" },
  {
    path: "default_provider",
    label: "Default provider",
    kind: "select",
    options: defaultProviderOptions.value,
  },
  { path: "system_prompt", label: "System prompt", kind: "textarea" },
]);

const activeFields = computed<FieldDef[]>(() => {
  switch (activeSection.value) {
    case "general":
      return generalFields.value;
    case "multimodal":
      return multimodalFields;
    case "agent":
      return agentFields;
    case "router":
      return routerFields;
    case "context":
      return contextFields;
    case "memory":
      return memoryFields;
    case "scheduler":
      return schedulerFields;
    default:
      return [];
  }
});

const activeSectionTitle = computed(
  () => sectionCopy[activeSection.value]?.title ?? "",
);

const activeSectionDescription = computed(
  () => sectionCopy[activeSection.value]?.description ?? "",
);

const validationVariant = computed<BadgeVariant>(() => {
  if (!validation.value) return "default";
  if (validation.value.errors > 0) return "destructive";
  if (validation.value.warnings > 0) return "warning";
  return "success";
});

const hasChanges = computed(() => !sameValue(draft.value, original.value));
const changeCount = computed(() => buildChanges().length);

watch(
  () => configDoc.value,
  (doc) => {
    if (!doc) return;
    resetDraftFromDocument();
  },
  { immediate: true },
);

let validateTimer: number | undefined;
watch(
  draft,
  () => {
    if (!configDoc.value) return;
    if (validateTimer !== undefined) window.clearTimeout(validateTimer);
    validateTimer = window.setTimeout(() => {
      validateDraft();
    }, 350);
  },
  { deep: true },
);

function isRecord(value: unknown): value is ConfigMap {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function cloneConfig(value: ConfigMap): ConfigMap {
  return JSON.parse(JSON.stringify(toRaw(value))) as ConfigMap;
}

function sameValue(a: unknown, b: unknown) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function resetDraftFromDocument() {
  if (!configDoc.value) return;
  original.value = cloneConfig(configDoc.value.redacted_data);
  draft.value = cloneConfig(configDoc.value.redacted_data);
  baseChecksum.value = configDoc.value.checksum;
  validation.value = null;
  validateDraft();
}

function getAtPath(root: ConfigMap, path: string): unknown {
  const parts = path.split(".");
  let current: unknown = root;
  for (const part of parts) {
    if (!isRecord(current)) return undefined;
    current = current[part];
  }
  return current;
}

function setAtPath(root: ConfigMap, path: string, value: unknown) {
  const parts = path.split(".");
  let current: ConfigMap = root;
  for (const part of parts.slice(0, -1)) {
    if (!isRecord(current[part])) current[part] = {};
    current = current[part] as ConfigMap;
  }
  current[parts[parts.length - 1]] = value;
}

function removeAtPath(root: ConfigMap, path: string) {
  const parts = path.split(".");
  let current: unknown = root;
  for (const part of parts.slice(0, -1)) {
    if (!isRecord(current)) return;
    current = current[part];
  }
  if (isRecord(current)) delete current[parts[parts.length - 1]];
}

function getValue(path: string) {
  return getAtPath(draft.value, path);
}

function fieldText(path: string) {
  const value = getValue(path);
  if (value === null || value === undefined) return "";
  return String(value);
}

function fieldBool(path: string) {
  return Boolean(getValue(path));
}

function updateText(path: string, value: string) {
  setAtPath(draft.value, path, value);
}

function updateNumber(path: string, raw: string) {
  if (raw.trim() === "") {
    setAtPath(draft.value, path, null);
    return;
  }
  const value = Number(raw);
  if (Number.isFinite(value)) setAtPath(draft.value, path, value);
}

function updateBool(path: string, value: boolean) {
  setAtPath(draft.value, path, value);
}

function fieldTriState(path: string) {
  const value = getValue(path);
  if (value === true) return "true";
  if (value === false) return "false";
  return "inherit";
}

function updateTriState(path: string, value: string) {
  if (value === "true") {
    setAtPath(draft.value, path, true);
    return;
  }
  if (value === "false") {
    setAtPath(draft.value, path, false);
    return;
  }
  setAtPath(draft.value, path, null);
}

function listText(path: string) {
  const value = getValue(path);
  return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "";
}

function updateList(path: string, raw: string) {
  const values = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  setAtPath(draft.value, path, values);
}

function providerPath(providerId: string, key: string) {
  return `providers.${providerId}.${key}`;
}

function providerModels(providerId: string) {
  const value = getValue(providerPath(providerId, "models"));
  return Array.isArray(value) ? value : [];
}

function updateProviderModelsArray(providerId: string, models: unknown[]) {
  setAtPath(draft.value, providerPath(providerId, "models"), models);
}

function providerModelName(model: unknown) {
  if (isRecord(model)) return String(model.name ?? "");
  return String(model ?? "");
}

function providerModelTags(model: unknown) {
  if (!isRecord(model) || !Array.isArray(model.tags)) return [];
  return model.tags.map((tag) => String(tag));
}

function providerModelCapabilities(model: unknown): ConfigMap {
  if (!isRecord(model) || !isRecord(model.capabilities)) return {};
  return model.capabilities;
}

function providerModelCapabilityEntries(model: unknown): CapabilityEntry[] {
  return Object.entries(providerModelCapabilities(model)).map(([key, value]) => ({
    key,
    value,
    type: capabilityValueType(value),
  }));
}

function capabilityValueType(value: unknown): CapabilityValueType {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  if (Array.isArray(value)) return "list";
  return "string";
}

function capabilityTextValue(value: unknown) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map((item) => String(item)).join("\n");
  return String(value);
}

function capabilityDefaultValue(type: CapabilityValueType) {
  switch (type) {
    case "boolean":
      return false;
    case "number":
      return 0;
    case "list":
      return [];
    case "null":
      return null;
    case "string":
      return "";
  }
}

function updateProviderModelName(providerId: string, index: number, value: string) {
  const models = [...providerModels(providerId)];
  const current = models[index];
  models[index] = isRecord(current) ? { ...current, name: value } : value;
  updateProviderModelsArray(providerId, models);
}

function updateProviderModelObject(
  providerId: string,
  index: number,
  updater: (model: ConfigMap) => void,
) {
  const models = [...providerModels(providerId)];
  const current = models[index];
  const model: ConfigMap = isRecord(current)
    ? { ...current }
    : { name: String(current ?? "") };
  if (!Array.isArray(model.tags)) model.tags = [];
  if (!isRecord(model.capabilities)) model.capabilities = {};
  updater(model);
  models[index] = model;
  updateProviderModelsArray(providerId, models);
}

function updateProviderModelTag(
  providerId: string,
  modelIndex: number,
  tagIndex: number,
  value: string,
) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const tags = Array.isArray(model.tags)
      ? model.tags.map((tag) => String(tag))
      : [];
    tags[tagIndex] = value;
    model.tags = tags.filter((tag) => tag.trim() !== "");
  });
}

function addProviderModelTag(providerId: string, modelIndex: number) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const tags = Array.isArray(model.tags)
      ? model.tags.map((tag) => String(tag))
      : [];
    tags.push("");
    model.tags = tags;
  });
}

function removeProviderModelTag(
  providerId: string,
  modelIndex: number,
  tagIndex: number,
) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const tags = Array.isArray(model.tags)
      ? model.tags.map((tag) => String(tag))
      : [];
    tags.splice(tagIndex, 1);
    model.tags = tags;
  });
}

function updateProviderModelCapabilityKey(
  providerId: string,
  modelIndex: number,
  oldKey: string,
  newKey: string,
) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const capabilities = isRecord(model.capabilities)
      ? { ...model.capabilities }
      : {};
    const trimmed = newKey.trim();
    if (!trimmed || trimmed === oldKey) return;
    const value = capabilities[oldKey];
    delete capabilities[oldKey];
    capabilities[trimmed] = value;
    model.capabilities = capabilities;
  });
}

function updateProviderModelCapabilityType(
  providerId: string,
  modelIndex: number,
  key: string,
  type: CapabilityValueType,
) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const capabilities = isRecord(model.capabilities)
      ? { ...model.capabilities }
      : {};
    capabilities[key] = capabilityDefaultValue(type);
    model.capabilities = capabilities;
  });
}

function updateProviderModelCapabilityValue(
  providerId: string,
  modelIndex: number,
  key: string,
  type: CapabilityValueType,
  raw: string | boolean,
) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const capabilities = isRecord(model.capabilities)
      ? { ...model.capabilities }
      : {};
    switch (type) {
      case "boolean":
        capabilities[key] = Boolean(raw);
        break;
      case "number": {
        const value = Number(raw);
        capabilities[key] = Number.isFinite(value) ? value : 0;
        break;
      }
      case "list":
        capabilities[key] = String(raw)
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean);
        break;
      case "null":
        capabilities[key] = null;
        break;
      case "string":
        capabilities[key] = String(raw);
        break;
    }
    model.capabilities = capabilities;
  });
}

function addProviderModelCapability(providerId: string, modelIndex: number) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const capabilities = isRecord(model.capabilities)
      ? { ...model.capabilities }
      : {};
    let nextIndex = Object.keys(capabilities).length + 1;
    let key = `capability_${nextIndex}`;
    while (key in capabilities) {
      nextIndex += 1;
      key = `capability_${nextIndex}`;
    }
    capabilities[key] = false;
    model.capabilities = capabilities;
  });
}

function removeProviderModelCapability(
  providerId: string,
  modelIndex: number,
  key: string,
) {
  updateProviderModelObject(providerId, modelIndex, (model) => {
    const capabilities = isRecord(model.capabilities)
      ? { ...model.capabilities }
      : {};
    delete capabilities[key];
    model.capabilities = capabilities;
  });
}

function addProviderModel(providerId: string) {
  updateProviderModelsArray(providerId, [
    ...providerModels(providerId),
    { name: "", tags: [], capabilities: {} },
  ]);
}

function removeProviderModel(providerId: string, index: number) {
  const models = [...providerModels(providerId)];
  models.splice(index, 1);
  updateProviderModelsArray(providerId, models);
}

function convertProviderModelToObject(providerId: string, index: number) {
  const models = [...providerModels(providerId)];
  const current = models[index];
  if (isRecord(current)) return;
  models[index] = { name: String(current ?? ""), tags: [], capabilities: {} };
  updateProviderModelsArray(providerId, models);
}

function convertProviderModelToString(providerId: string, index: number) {
  const models = [...providerModels(providerId)];
  const current = models[index];
  models[index] = providerModelName(current);
  updateProviderModelsArray(providerId, models);
}

function addProvider() {
  const id = newProviderId.value.trim();
  if (!id || providerIds.value.includes(id)) return;
  if (!isRecord(getValue("providers"))) setAtPath(draft.value, "providers", {});
  setAtPath(draft.value, `providers.${id}`, {
    type: "openai-compatible",
    api_key: "",
    base_url: "",
    stream_responses: true,
    models: [],
  });
  if (!fieldText("default_provider")) updateText("default_provider", id);
  newProviderId.value = "";
}

function removeProvider(providerId: string) {
  removeAtPath(draft.value, `providers.${providerId}`);
  if (fieldText("default_provider") === providerId) {
    updateText("default_provider", providerIds.value.find((id) => id !== providerId) ?? "");
  }
}

function clearSecret(path: string) {
  setAtPath(draft.value, path, "");
}

function secretInputValue(path: string) {
  if (redactedPaths.value.has(path) && fieldText(path) === "***") return "";
  return fieldText(path);
}

function secretPlaceholder(path: string) {
  return redactedPaths.value.has(path) ? "unchanged" : "";
}

function renderFieldKey(field: FieldDef) {
  return field.path.replaceAll(".", "_");
}

function isWideField(field: FieldDef) {
  return field.wide || field.kind === "textarea" || field.kind === "list";
}

function fieldIssueCount(path: string) {
  return validation.value?.issues.filter((issue) => issue.path === path).length ?? 0;
}

function buildChanges() {
  const changes: ConfigPatchChange[] = [];
  diffConfig("", original.value, draft.value, changes);
  return changes;
}

function diffConfig(
  path: string,
  before: unknown,
  after: unknown,
  out: ConfigPatchChange[],
) {
  if (before === undefined && after === undefined) return;
  if (before === undefined) {
    out.push({ path, value: after });
    return;
  }
  if (after === undefined) {
    out.push({ path, remove: true });
    return;
  }

  if (isRecord(before) && isRecord(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const key of keys) {
      diffConfig(path ? `${path}.${key}` : key, before[key], after[key], out);
    }
    return;
  }

  if (sameValue(before, after)) return;
  if (redactedPaths.value.has(path) && before === "***" && after === "***") return;
  out.push({
    path,
    value: after,
    secret_action: redactedPaths.value.has(path) ? "replace" : undefined,
  });
}

async function validateDraft() {
  if (!configDoc.value) return;
  validating.value = true;
  try {
    validation.value = await api.post<ConfigValidateResponse>(
      "/config/validate",
      { data: draft.value },
    );
  } catch {
    /* Keep the previous validation result while the server reports transport errors. */
  } finally {
    validating.value = false;
  }
}

function requestSave() {
  pendingChanges.value = buildChanges();
  if (!pendingChanges.value.length) return;
  showSaveDialog.value = true;
}

function confirmSave() {
  showSaveDialog.value = false;
  saveMutation.mutate(
    {
      expected_checksum: baseChecksum.value,
      changes: pendingChanges.value,
    },
    {
      onSuccess: (data) => {
        original.value = cloneConfig(draft.value);
        baseChecksum.value = data.checksum;
        validation.value = {
          errors: data.validation.errors,
          warnings: data.validation.warnings,
          ok: data.validation.errors === 0,
          issues: data.validation.issues,
        };
      },
    },
  );
}
</script>

<template>
  <div class="config-page">
    <Alert v-if="configError" variant="destructive">
      Failed to load config: {{ configError.message }}
    </Alert>

    <div v-if="configLoading || schemaLoading" class="loading">Loading...</div>

    <template v-if="configDoc">
      <div class="config-header">
        <div class="config-title-block">
          <h1>Config</h1>
          <div class="config-meta">
            <span><code>{{ configDoc.path }}</code></span>
            <span>Modified {{ configDoc.mtime }}</span>
            <Badge variant="outline">{{ configDoc.checksum.slice(0, 19) }}</Badge>
          </div>
        </div>
        <div class="config-actions">
          <Badge v-if="hasChanges" variant="warning">{{ changeCount }} changes</Badge>
          <Badge v-else variant="secondary">Saved</Badge>
          <Badge :variant="validationVariant">
            <Spinner v-if="validating" size="sm" />
            <span v-else>{{ validation?.ok ? "Valid" : "Needs attention" }}</span>
          </Badge>
          <Button variant="outline" :disabled="!hasChanges || saveMutation.isPending.value" @click="resetDraftFromDocument">
            Discard
          </Button>
          <Button :disabled="!hasChanges || saveMutation.isPending.value" @click="requestSave">
            <Spinner v-if="saveMutation.isPending.value" size="sm" />
            Save
          </Button>
        </div>
      </div>

      <Tabs :tabs="modeTabs" v-model="mode" />

      <div v-if="mode === 'form'" class="config-workspace">
        <aside class="section-nav">
          <button
            v-for="section in sections"
            :key="section.id"
            class="section-button"
            :class="{ active: activeSection === section.id }"
            @click="activeSection = section.id"
          >
            {{ section.label }}
          </button>

          <!-- Collapsible Plugins group -->
          <div class="nav-group">
            <button
              class="section-button group-toggle"
              :class="{ active: isPluginSection(activeSection) }"
              @click="pluginsExpanded = !pluginsExpanded"
            >
              <span>Plugins</span>
              <span class="toggle-arrow" :class="{ expanded: pluginsExpanded }">▸</span>
            </button>
            <div v-if="pluginsExpanded" class="group-children">
              <button
                v-for="plugin in pluginList"
                :key="plugin.id"
                class="section-button plugin-nav-item"
                :class="{ active: activeSection === `plugin:${plugin.id}` }"
                @click="selectPlugin(plugin.id)"
              >
                {{ plugin.name || plugin.id }}
              </button>
              <div v-if="!pluginList.length" class="nav-muted">No plugins</div>
            </div>
          </div>
        </aside>

        <main class="section-panel">
          <template v-if="activeSection !== 'providers' && activeSection !== 'channels'">
            <div class="section-heading">
              <h2>{{ activeSectionTitle }}</h2>
              <p>{{ activeSectionDescription }}</p>
            </div>
            <div class="field-grid">
              <div
                v-for="field in activeFields"
                :key="field.path"
                class="field-row"
                :class="{ wide: isWideField(field) }"
              >
                <label :for="renderFieldKey(field)">
                  <span>{{ field.label }}</span>
                  <code>{{ field.path }}</code>
                </label>
                <select
                  v-if="field.kind === 'select'"
                  :id="renderFieldKey(field)"
                  :value="fieldText(field.path)"
                  @change="updateText(field.path, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="option in field.options" :key="option" :value="option">
                    {{ option || "auto" }}
                  </option>
                </select>
                <select
                  v-else-if="field.kind === 'tri-state'"
                  :id="renderFieldKey(field)"
                  :value="fieldTriState(field.path)"
                  @change="updateTriState(field.path, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="inherit">inherit</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
                <input
                  v-else-if="field.kind === 'boolean'"
                  :id="renderFieldKey(field)"
                  type="checkbox"
                  :checked="fieldBool(field.path)"
                  @change="updateBool(field.path, ($event.target as HTMLInputElement).checked)"
                />
                <input
                  v-else-if="field.kind === 'number'"
                  :id="renderFieldKey(field)"
                  type="number"
                  :value="fieldText(field.path)"
                  @input="updateNumber(field.path, ($event.target as HTMLInputElement).value)"
                />
                <Textarea
                  v-else-if="field.kind === 'textarea'"
                  :id="renderFieldKey(field)"
                  :model-value="fieldText(field.path)"
                  :rows="5"
                  @update:model-value="updateText(field.path, $event)"
                />
                <Textarea
                  v-else-if="field.kind === 'list'"
                  :id="renderFieldKey(field)"
                  :model-value="listText(field.path)"
                  :rows="3"
                  @update:model-value="updateList(field.path, $event)"
                />
                <div v-else-if="field.kind === 'secret'" class="secret-control">
                  <input
                    :id="renderFieldKey(field)"
                    type="password"
                    :placeholder="secretPlaceholder(field.path)"
                    :value="secretInputValue(field.path)"
                    @input="updateText(field.path, ($event.target as HTMLInputElement).value)"
                  />
                  <Button variant="outline" size="sm" @click="clearSecret(field.path)">
                    Clear
                  </Button>
                </div>
                <input
                  v-else
                  :id="renderFieldKey(field)"
                  type="text"
                  :value="fieldText(field.path)"
                  @input="updateText(field.path, ($event.target as HTMLInputElement).value)"
                />
                <span v-if="fieldIssueCount(field.path)" class="field-issue">{{ fieldIssueCount(field.path) }}</span>
              </div>
            </div>
          </template>

          <template v-if="activeSection === 'channels'">
            <div class="section-heading">
              <h2>{{ activeSectionTitle }}</h2>
              <p>{{ activeSectionDescription }}</p>
            </div>

            <div
              v-for="group in channelFieldGroups"
              :key="group.id"
              class="channel-panel"
            >
              <div class="channel-header">
                <div>
                  <h3>{{ group.title }}</h3>
                  <p>{{ group.description }}</p>
                </div>
                <code>{{ group.id }}</code>
              </div>

              <div class="field-grid channel-fields">
                <div
                  v-for="field in group.fields"
                  :key="field.path"
                  class="field-row"
                  :class="{ wide: isWideField(field) }"
                >
                  <label :for="renderFieldKey(field)">
                    <span>{{ field.label }}</span>
                    <code>{{ field.path }}</code>
                  </label>
                  <select
                    v-if="field.kind === 'select'"
                    :id="renderFieldKey(field)"
                    :value="fieldText(field.path)"
                    @change="updateText(field.path, ($event.target as HTMLSelectElement).value)"
                  >
                    <option v-for="option in field.options" :key="option" :value="option">
                      {{ option || "auto" }}
                    </option>
                  </select>
                  <select
                    v-else-if="field.kind === 'tri-state'"
                    :id="renderFieldKey(field)"
                    :value="fieldTriState(field.path)"
                    @change="updateTriState(field.path, ($event.target as HTMLSelectElement).value)"
                  >
                    <option value="inherit">inherit</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                  <input
                    v-else-if="field.kind === 'boolean'"
                    :id="renderFieldKey(field)"
                    type="checkbox"
                    :checked="fieldBool(field.path)"
                    @change="updateBool(field.path, ($event.target as HTMLInputElement).checked)"
                  />
                  <input
                    v-else-if="field.kind === 'number'"
                    :id="renderFieldKey(field)"
                    type="number"
                    :value="fieldText(field.path)"
                    @input="updateNumber(field.path, ($event.target as HTMLInputElement).value)"
                  />
                  <Textarea
                    v-else-if="field.kind === 'textarea'"
                    :id="renderFieldKey(field)"
                    :model-value="fieldText(field.path)"
                    :rows="5"
                    @update:model-value="updateText(field.path, $event)"
                  />
                  <Textarea
                    v-else-if="field.kind === 'list'"
                    :id="renderFieldKey(field)"
                    :model-value="listText(field.path)"
                    :rows="3"
                    @update:model-value="updateList(field.path, $event)"
                  />
                  <div v-else-if="field.kind === 'secret'" class="secret-control">
                    <input
                      :id="renderFieldKey(field)"
                      type="password"
                      :placeholder="secretPlaceholder(field.path)"
                      :value="secretInputValue(field.path)"
                      @input="updateText(field.path, ($event.target as HTMLInputElement).value)"
                    />
                    <Button variant="outline" size="sm" @click="clearSecret(field.path)">
                      Clear
                    </Button>
                  </div>
                  <input
                    v-else
                    :id="renderFieldKey(field)"
                    type="text"
                    :value="fieldText(field.path)"
                    @input="updateText(field.path, ($event.target as HTMLInputElement).value)"
                  />
                  <span v-if="fieldIssueCount(field.path)" class="field-issue">{{ fieldIssueCount(field.path) }}</span>
                </div>
              </div>
            </div>
          </template>

          <template v-if="activeSection === 'providers'">
            <div class="section-heading">
              <h2>Providers</h2>
              <p>Manage provider endpoints, credentials, model lists, and the default provider.</p>
            </div>
            <div class="add-provider">
              <input
                v-model="newProviderId"
                type="text"
                placeholder="provider id"
                @keydown.enter.prevent="addProvider"
              />
              <Button size="sm" @click="addProvider">Add</Button>
            </div>
            <div v-if="!providerIds.length" class="empty">
              No providers configured.
            </div>
            <div v-for="providerId in providerIds" :key="providerId" class="provider-panel">
              <div class="provider-header">
                <div>
                  <h3>{{ providerId }}</h3>
                  <code>providers.{{ providerId }}</code>
                </div>
                <Button variant="outline" size="sm" @click="removeProvider(providerId)">
                  Remove
                </Button>
              </div>
              <div class="field-grid provider-fields">
                <div class="field-row">
                  <label>
                    <span>Type</span>
                    <code>{{ providerPath(providerId, "type") }}</code>
                  </label>
                  <select
                    :value="fieldText(providerPath(providerId, 'type'))"
                    @change="updateText(providerPath(providerId, 'type'), ($event.target as HTMLSelectElement).value)"
                  >
                    <option v-for="option in providerTypeOptions" :key="option" :value="option">
                      {{ option }}
                    </option>
                  </select>
                </div>
                <div class="field-row">
                  <label>
                    <span>Base URL</span>
                    <code>{{ providerPath(providerId, "base_url") }}</code>
                  </label>
                  <input
                    type="text"
                    :value="fieldText(providerPath(providerId, 'base_url'))"
                    @input="updateText(providerPath(providerId, 'base_url'), ($event.target as HTMLInputElement).value)"
                  />
                </div>
                <div class="field-row">
                  <label>
                    <span>API key</span>
                    <code>{{ providerPath(providerId, "api_key") }}</code>
                  </label>
                  <div class="secret-control">
                    <input
                      type="password"
                      :placeholder="redactedPaths.has(providerPath(providerId, 'api_key')) ? 'unchanged' : ''"
                      :value="redactedPaths.has(providerPath(providerId, 'api_key')) && fieldText(providerPath(providerId, 'api_key')) === '***' ? '' : fieldText(providerPath(providerId, 'api_key'))"
                      @input="updateText(providerPath(providerId, 'api_key'), ($event.target as HTMLInputElement).value)"
                    />
                    <Button variant="outline" size="sm" @click="clearSecret(providerPath(providerId, 'api_key'))">
                      Clear
                    </Button>
                  </div>
                </div>
                <div class="field-row compact">
                  <label>
                    <span>Stream responses</span>
                    <code>{{ providerPath(providerId, "stream_responses") }}</code>
                  </label>
                  <input
                    type="checkbox"
                    :checked="fieldBool(providerPath(providerId, 'stream_responses'))"
                    @change="updateBool(providerPath(providerId, 'stream_responses'), ($event.target as HTMLInputElement).checked)"
                  />
                </div>
                <div class="field-row compact">
                  <label>
                    <span>Merge system messages</span>
                    <code>{{ providerPath(providerId, "merge_system_messages") }}</code>
                  </label>
                  <input
                    type="checkbox"
                    :checked="fieldBool(providerPath(providerId, 'merge_system_messages'))"
                    @change="updateBool(providerPath(providerId, 'merge_system_messages'), ($event.target as HTMLInputElement).checked)"
                  />
                </div>
                <div class="field-row wide">
                  <label>
                    <span>Models</span>
                    <code>{{ providerPath(providerId, "models") }}</code>
                  </label>
                  <div class="models-editor">
                    <div
                      v-for="(model, index) in providerModels(providerId)"
                      :key="`${providerId}-model-${index}`"
                      class="model-card"
                    >
                      <div class="model-card-header">
                        <div>
                          <span class="model-title">Model {{ index + 1 }}</span>
                          <Badge variant="outline">
                            {{ isRecord(model) ? "object" : "string" }}
                          </Badge>
                        </div>
                        <div class="model-actions">
                          <Button
                            v-if="!isRecord(model)"
                            variant="outline"
                            size="sm"
                            @click="convertProviderModelToObject(providerId, index)"
                          >
                            Advanced
                          </Button>
                          <Button
                            v-else
                            variant="outline"
                            size="sm"
                            @click="convertProviderModelToString(providerId, index)"
                          >
                            Simple
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            @click="removeProviderModel(providerId, index)"
                          >
                            Remove
                          </Button>
                        </div>
                      </div>

                      <div class="model-field-grid">
                        <div class="field-row">
                          <label>
                            <span>Name</span>
                            <code>{{ providerPath(providerId, `models[${index}].name`) }}</code>
                          </label>
                          <input
                            type="text"
                            :value="providerModelName(model)"
                            @input="updateProviderModelName(providerId, index, ($event.target as HTMLInputElement).value)"
                          />
                        </div>

                        <template v-if="isRecord(model)">
                          <div class="field-row">
                            <label>
                              <span>Tags</span>
                              <code>{{ providerPath(providerId, `models[${index}].tags`) }}</code>
                            </label>
                            <div class="tag-editor">
                              <div
                                v-for="(tag, tagIndex) in providerModelTags(model)"
                                :key="`${providerId}-${index}-tag-${tagIndex}`"
                                class="tag-row"
                              >
                                <input
                                  type="text"
                                  :value="tag"
                                  @input="updateProviderModelTag(providerId, index, tagIndex, ($event.target as HTMLInputElement).value)"
                                />
                                <Button
                                  variant="outline"
                                  size="sm"
                                  @click="removeProviderModelTag(providerId, index, tagIndex)"
                                >
                                  Remove
                                </Button>
                              </div>
                              <Button
                                size="sm"
                                variant="outline"
                                @click="addProviderModelTag(providerId, index)"
                              >
                                Add tag
                              </Button>
                            </div>
                          </div>
                          <div class="field-row wide">
                            <label>
                              <span>Capabilities</span>
                              <code>{{ providerPath(providerId, `models[${index}].capabilities`) }}</code>
                            </label>
                            <div class="capability-editor">
                              <div
                                v-for="entry in providerModelCapabilityEntries(model)"
                                :key="`${providerId}-${index}-cap-${entry.key}`"
                                class="capability-row"
                              >
                                <input
                                  class="capability-key"
                                  type="text"
                                  :value="entry.key"
                                  @change="updateProviderModelCapabilityKey(providerId, index, entry.key, ($event.target as HTMLInputElement).value)"
                                />
                                <select
                                  class="capability-type"
                                  :value="entry.type"
                                  @change="updateProviderModelCapabilityType(providerId, index, entry.key, ($event.target as HTMLSelectElement).value as CapabilityValueType)"
                                >
                                  <option value="boolean">bool</option>
                                  <option value="number">number</option>
                                  <option value="string">string</option>
                                  <option value="list">list</option>
                                  <option value="null">null</option>
                                </select>
                                <div class="capability-value">
                                  <label v-if="entry.type === 'boolean'" class="checkbox-value">
                                    <input
                                      type="checkbox"
                                      :checked="Boolean(entry.value)"
                                      @change="updateProviderModelCapabilityValue(providerId, index, entry.key, entry.type, ($event.target as HTMLInputElement).checked)"
                                    />
                                    <span>{{ Boolean(entry.value) ? "true" : "false" }}</span>
                                  </label>
                                  <input
                                    v-else-if="entry.type === 'number'"
                                    type="number"
                                    :value="capabilityTextValue(entry.value)"
                                    @input="updateProviderModelCapabilityValue(providerId, index, entry.key, entry.type, ($event.target as HTMLInputElement).value)"
                                  />
                                  <Textarea
                                    v-else-if="entry.type === 'list'"
                                    :model-value="capabilityTextValue(entry.value)"
                                    :rows="3"
                                    @update:model-value="updateProviderModelCapabilityValue(providerId, index, entry.key, entry.type, $event)"
                                  />
                                  <input
                                    v-else-if="entry.type === 'string'"
                                    type="text"
                                    :value="capabilityTextValue(entry.value)"
                                    @input="updateProviderModelCapabilityValue(providerId, index, entry.key, entry.type, ($event.target as HTMLInputElement).value)"
                                  />
                                  <span v-else class="null-value">null</span>
                                </div>
                                <Button
                                  class="capability-remove"
                                  variant="outline"
                                  size="sm"
                                  @click="removeProviderModelCapability(providerId, index, entry.key)"
                                >
                                  Remove
                                </Button>
                              </div>
                              <Button
                                size="sm"
                                variant="outline"
                                @click="addProviderModelCapability(providerId, index)"
                              >
                                Add capability
                              </Button>
                            </div>
                          </div>
                        </template>
                      </div>
                    </div>

                    <Button size="sm" variant="outline" @click="addProviderModel(providerId)">
                      Add model
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- Plugin configuration form -->
          <template v-if="isPluginSection(activeSection) && activePluginFields.length">
            <div class="section-heading">
              <h2>{{ activePluginFields.length ? pluginIdFromSection(activeSection) : 'Plugins' }}</h2>
              <p>{{ sectionCopy.plugins.description }}</p>
            </div>
            <div class="field-grid">
              <div
                v-for="field in activePluginFields"
                :key="field.path"
                class="field-row"
                :class="{
                  wide: field.kind === 'array-string' || field.kind === 'array-number' || field.kind === 'secret',
                }"
              >
                <label :for="`plugin_${field.path.replaceAll('.', '_')}`">
                  <span>{{ field.label }}</span>
                  <code>{{ field.path }}</code>
                </label>

                <select
                  v-if="field.kind === 'select'"
                  :id="`plugin_${field.path.replaceAll('.', '_')}`"
                  :value="fieldText(`${pluginIdFromSection(activeSection)}.${field.path}`)"
                  @change="updateText(`${pluginIdFromSection(activeSection)}.${field.path}`, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="opt in field.options" :key="opt" :value="opt">
                    {{ opt || "auto" }}
                  </option>
                </select>

                <input
                  v-else-if="field.kind === 'boolean'"
                  :id="`plugin_${field.path.replaceAll('.', '_')}`"
                  type="checkbox"
                  :checked="fieldBool(`${pluginIdFromSection(activeSection)}.${field.path}`)"
                  @change="updateBool(`${pluginIdFromSection(activeSection)}.${field.path}`, ($event.target as HTMLInputElement).checked)"
                />

                <input
                  v-else-if="field.kind === 'number' || field.kind === 'integer'"
                  :id="`plugin_${field.path.replaceAll('.', '_')}`"
                  type="number"
                  :step="field.kind === 'integer' ? 1 : 'any'"
                  :value="fieldText(`${pluginIdFromSection(activeSection)}.${field.path}`)"
                  @input="updateNumber(`${pluginIdFromSection(activeSection)}.${field.path}`, ($event.target as HTMLInputElement).value)"
                />

                <div v-else-if="field.kind === 'secret'" class="secret-control">
                  <input
                    :id="`plugin_${field.path.replaceAll('.', '_')}`"
                    type="password"
                    :placeholder="redactedPaths.has(`${pluginIdFromSection(activeSection)}.${field.path}`) ? 'unchanged' : ''"
                    :value="redactedPaths.has(`${pluginIdFromSection(activeSection)}.${field.path}`) && fieldText(`${pluginIdFromSection(activeSection)}.${field.path}`) === '***' ? '' : fieldText(`${pluginIdFromSection(activeSection)}.${field.path}`)"
                    @input="updateText(`${pluginIdFromSection(activeSection)}.${field.path}`, ($event.target as HTMLInputElement).value)"
                  />
                  <Button variant="outline" size="sm" @click="clearSecret(`${pluginIdFromSection(activeSection)}.${field.path}`)">
                    Clear
                  </Button>
                </div>

                <Textarea
                  v-else-if="field.kind === 'array-string' || field.kind === 'array-number'"
                  :id="`plugin_${field.path.replaceAll('.', '_')}`"
                  :model-value="listText(`${pluginIdFromSection(activeSection)}.${field.path}`)"
                  :rows="3"
                  @update:model-value="updateList(`${pluginIdFromSection(activeSection)}.${field.path}`, $event)"
                />

                <!-- Default: text -->
                <input
                  v-else
                  :id="`plugin_${field.path.replaceAll('.', '_')}`"
                  type="text"
                  :value="fieldText(`${pluginIdFromSection(activeSection)}.${field.path}`)"
                  @input="updateText(`${pluginIdFromSection(activeSection)}.${field.path}`, ($event.target as HTMLInputElement).value)"
                />
              </div>
            </div>
          </template>

          <template v-if="isPluginSection(activeSection) && !activePluginFields.length">
            <div class="section-heading">
              <h2>{{ pluginIdFromSection(activeSection) }}</h2>
              <p>{{ sectionCopy.plugins.description }}</p>
            </div>
            <p class="muted">This plugin has no configurable fields.</p>
          </template>

        </main>

        <aside class="status-panel">
          <div class="status-block">
            <h2>Validation</h2>
            <Badge :variant="validationVariant">
              {{ validation?.ok ? "OK" : `${validation?.errors ?? 0} errors, ${validation?.warnings ?? 0} warnings` }}
            </Badge>
          </div>
          <div v-if="validation?.issues.length" class="issue-list">
            <div v-for="(issue, index) in validation.issues" :key="index" class="issue-item">
              <Badge :variant="issue.severity === 'error' ? 'destructive' : 'warning'">
                {{ issue.severity }}
              </Badge>
              <span>{{ issue.message }}</span>
              <code v-if="issue.path">{{ issue.path }}</code>
            </div>
          </div>
          <div v-else class="muted">
            No validation issues.
          </div>
          <div class="status-block">
            <h2>Changes</h2>
            <span class="muted">{{ changeCount }} pending path updates</span>
          </div>
        </aside>
      </div>

      <div v-if="mode === 'yaml'" class="yaml-view">
        <div class="section-heading">
          <h2>YAML Preview</h2>
          <p>This preview is redacted. Graphical saves use path-level patches.</p>
        </div>
        <pre class="yaml-pre">{{ configDoc.content }}</pre>
      </div>

      <div v-if="mode === 'schema' && schemaData" class="schema-view">
        <table class="config-table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Type</th>
              <th>Current</th>
              <th>Default</th>
              <th>Constraints</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in schemaData.entries" :key="entry.path">
              <td><code>{{ entry.path }}</code></td>
              <td>{{ entry.type }}</td>
              <td>
                <code v-if="currentValueByPath.get(entry.path)" class="value-code">
                  {{ currentValueByPath.get(entry.path) }}
                </code>
                <span v-else class="muted">-</span>
              </td>
              <td>
                <code v-if="entry.default">{{ entry.default }}</code>
                <span v-else class="muted">-</span>
              </td>
              <td>{{ entry.constraints }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <ConfirmDialog
      v-model:open="showSaveDialog"
      title="Save Configuration"
      :description="`This will apply ${pendingChanges.length} path-level change(s), write a backup, and require restart.`"
      confirm-label="Save"
      :loading="saveMutation.isPending.value"
      @confirm="confirmSave"
    />
  </div>
</template>

<style scoped>
.config-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.loading,
.muted {
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.config-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--color-border);
  padding-bottom: 1rem;
}

.config-title-block h1,
.section-heading h2,
.status-block h2 {
  margin: 0;
  font-size: 1rem;
  line-height: 1.25;
}

.config-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 0.5rem;
  color: var(--color-muted-foreground);
  font-size: 0.75rem;
}

code {
  background: var(--color-muted);
  border-radius: var(--radius-sm);
  padding: 0.0625rem 0.3125rem;
  font-family: var(--font-mono);
  font-size: 0.72rem;
}

.config-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.config-workspace {
  display: grid;
  grid-template-columns: 176px minmax(0, 1fr) 280px;
  gap: 1rem;
  align-items: start;
}

.section-nav,
.status-panel {
  position: sticky;
  top: 1rem;
}

.section-nav {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.section-button {
  width: 100%;
  border: 0;
  border-left: 3px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-muted-foreground);
  cursor: pointer;
  font-size: 0.8125rem;
  padding: 0.5rem 0.625rem;
  text-align: left;
}

.section-button:hover,
.section-button.active {
  background: var(--color-accent);
  color: var(--color-accent-foreground);
}

.section-button.active {
  border-left-color: var(--color-primary);
  box-shadow: inset 6px 0 10px -12px var(--color-primary);
}

/* Collapsible Plugins group */
.nav-group {
  display: flex;
  flex-direction: column;
}

.group-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.toggle-arrow {
  font-size: 0.625rem;
  transition: transform 0.15s;
}

.toggle-arrow.expanded {
  transform: rotate(90deg);
}

.group-children {
  display: flex;
  flex-direction: column;
  gap: 0.0625rem;
  padding-left: 0.75rem;
  border-left: 1px solid var(--color-border-subtle);
  margin-left: 0.5rem;
}

.plugin-nav-item {
  font-size: 0.75rem;
  padding: 0.375rem 0.5rem;
}

.nav-muted {
  color: var(--color-muted-foreground);
  font-size: 0.7rem;
  padding: 0.375rem 0.5rem;
}

.section-panel,
.status-panel {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-card);
  padding: 1rem;
}

.section-heading {
  margin-bottom: 1rem;
}

.section-heading p {
  margin: 0.375rem 0 0;
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.875rem 1rem;
}

.field-row {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 0;
}

.field-row.wide {
  grid-column: 1 / -1;
}

.field-row.compact {
  justify-content: end;
}

.field-row label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  color: var(--color-foreground);
  font-size: 0.8125rem;
  font-weight: 500;
}

.field-row label code {
  color: var(--color-muted-foreground);
  font-weight: 400;
  max-width: 50%;
  overflow-wrap: anywhere;
  text-align: right;
}

input[type="text"],
input[type="password"],
input[type="number"],
select {
  width: 100%;
  height: 32px;
  border: 1px solid var(--color-input);
  border-radius: var(--radius-md);
  background: var(--color-background);
  color: var(--color-foreground);
  font-size: 0.8125rem;
  outline: none;
  padding: 0 0.5rem;
}

input[type="checkbox"] {
  width: 18px;
  height: 18px;
}

input:focus,
select:focus {
  border-color: var(--color-ring);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-ring) 20%, transparent);
}

.field-issue {
  color: var(--color-destructive);
  font-size: 0.75rem;
}

.add-provider,
.secret-control {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.add-provider {
  margin-bottom: 1rem;
  max-width: 28rem;
}

.secret-control input {
  min-width: 0;
}

.channel-panel {
  border-top: 1px solid var(--color-border);
  padding-top: 1rem;
}

.channel-panel + .channel-panel {
  margin-top: 1.25rem;
}

.channel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.channel-header h3 {
  margin: 0;
  font-size: 0.9375rem;
}

.channel-header p {
  margin: 0.25rem 0 0;
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.provider-panel {
  border-top: 1px solid var(--color-border);
  padding-top: 1rem;
}

.provider-panel + .provider-panel {
  margin-top: 1rem;
}

.provider-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}

.provider-header h3 {
  margin: 0 0 0.25rem;
  font-size: 0.9375rem;
}

.models-editor {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.model-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-background);
  padding: 0.875rem;
}

.model-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.875rem;
}

.model-card-header > div {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.model-title {
  font-size: 0.8125rem;
  font-weight: 600;
}

.model-actions {
  justify-content: flex-end;
}

.model-field-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.75rem 1rem;
}

.model-field-grid .wide {
  grid-column: 1 / -1;
}

.tag-editor,
.capability-editor {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.tag-row,
.capability-row {
  display: grid;
  gap: 0.5rem;
  align-items: start;
}

.tag-row {
  grid-template-columns: minmax(0, 1fr) auto;
}

.capability-row {
  grid-template-columns: minmax(12rem, 1fr) 7rem minmax(7rem, 0.55fr) auto;
}

.capability-key,
.capability-type {
  min-width: 0;
}

.capability-value {
  min-width: 0;
}

.checkbox-value {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 32px;
  color: var(--color-foreground);
  font-size: 0.8125rem;
}

.checkbox-value input[type="checkbox"] {
  flex: 0 0 auto;
}

.capability-remove {
  align-self: start;
  min-height: 32px;
}

.null-value {
  display: inline-flex;
  align-items: center;
  height: 32px;
  color: var(--color-muted-foreground);
  font-family: var(--font-mono);
  font-size: 0.8125rem;
}

.status-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.status-block {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.issue-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.issue-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.25rem;
  border-top: 1px solid var(--color-border);
  padding-top: 0.5rem;
  font-size: 0.75rem;
}

.empty {
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
  padding: 1.5rem;
  text-align: center;
}

.yaml-pre {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
  margin: 0;
  overflow-x: auto;
  padding: 1rem;
  white-space: pre-wrap;
  word-break: break-word;
}

.config-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

.config-table th {
  text-align: left;
  padding: 0.5rem;
  border-bottom: 1px solid var(--color-border);
  font-weight: 600;
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.config-table td {
  padding: 0.5rem;
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
}

.value-code {
  display: inline-block;
  max-width: 32rem;
  overflow-wrap: anywhere;
}

@media (max-width: 1100px) {
  .config-workspace {
    grid-template-columns: 156px minmax(0, 1fr);
  }

  .status-panel {
    grid-column: 1 / -1;
    position: static;
  }
}

@media (max-width: 760px) {
  .config-header {
    flex-direction: column;
  }

  .config-actions {
    justify-content: flex-start;
  }

  .config-workspace,
  .field-grid,
  .model-field-grid,
  .tag-row,
  .capability-row {
    grid-template-columns: 1fr;
  }

  .section-nav {
    position: static;
    flex-direction: row;
    overflow-x: auto;
  }

  .section-button {
    white-space: nowrap;
  }
}
</style>
