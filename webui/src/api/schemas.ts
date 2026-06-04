export interface ApiError {
  status: number;
  code?: string;
  detail: string;
  requestId?: string;
}

export interface BootstrapResponse {
  app_name: string;
  version: string;
  api_base: string;
  webui_base: string;
  auth: {
    required: boolean;
    mode: "none" | "password" | "bearer";
    api_token_supported?: boolean;
    session_cookie?: boolean;
  };
  features: { id: string; route: string; label: string; scope: string }[];
  server_time: string;
}

export interface AuthSessionResponse {
  authenticated: boolean;
  auth_required: boolean;
  mode: "none" | "password" | "bearer";
  expires_at: string;
}

export interface AuthLoginRequest {
  password: string;
}

export interface AuthLoginResponse {
  authenticated: boolean;
  mode: "password";
  expires_at: string;
}

export interface StatusResponse {
  app: {
    name: string;
    version: string;
    debug: boolean;
    started: boolean;
    started_at: string;
    uptime_seconds: number;
    pid: number;
  };
  resources: {
    cpu_percent: number;
    memory_rss_bytes: number;
    memory_percent: number;
    disk_free_bytes: number;
    db_size_bytes: number;
    workspace_size_bytes: number;
  };
  services: Record<string, string>;
  usage: {
    input_tokens: number;
    output_tokens: number;
    cached_tokens: number;
    reasoning_tokens: number;
    estimated_cost: number | null;
    currency: string | null;
  };
}

export interface ConfigCurrentResponse {
  content: string;
  checksum: string;
  path: string;
  mtime: string;
  entries: { path: string; type: string; value: string }[];
}

export interface ConfigDocumentResponse {
  content: string;
  checksum: string;
  path: string;
  mtime: string;
  data: Record<string, unknown>;
  redacted_data: Record<string, unknown>;
  redacted_paths: string[];
  entries: { path: string; type: string; value: string }[];
}

export interface ConfigSchemaResponse {
  entries: { path: string; type: string; default: string; constraints: string }[];
}

export interface ConfigValidateResponse {
  errors: number;
  warnings: number;
  ok: boolean;
  issues: { severity: string; message: string; path: string }[];
}

export interface CronJob {
  job_id: string;
  platform: string;
  chat_id: string;
  mode: string;
  prompt: string;
  is_active: boolean;
  next_fire_at: string | null;
  run_count: number;
  created_at: string;
  session_mode: string;
  session_name: string | null;
  session_key: string;
  chat_type: string;
  last_fired_at: string | null;
  failure_count: number;
  last_error: string | null;
  claimed_at: string | null;
  workspace_id: string | null;
  fire_at: string | null;
  interval_seconds: number | null;
  cron_expression: string | null;
  max_runs: number | null;
  created_by_user_id: string;
  created_from_session_id: string;
  created_from_chat_address: string;
}

export interface CronListResponse {
  jobs: CronJob[];
}

export interface SessionSummary {
  session_id: string;
  session_key_kind: string;
  workspace_id: string | null;
  created_at: string;
  last_active_at: string;
  turn_count: number;
  metadata: Record<string, unknown>;
}

export interface SessionListResponse {
  sessions: SessionSummary[];
}

export interface Turn {
  turn_id: number;
  role: string;
  content: string;
  source: string;
  created_at: string;
  metadata: Record<string, unknown>;
  sentinel_action: string | null;
  sentinel_suppressed: boolean;
}

export interface SessionHistoryResponse {
  session_id: string;
  turns: Turn[];
}

export interface MessageDelivery {
  delivery_id: string;
  target_chat_address: string;
  platform: string;
  target_type: string;
  target_id: string;
  source_session_id: string;
  source_chat_address: string;
  source_user_id: string;
  source: string;
  delivery_mode: string;
  status: string;
  message_id: string;
  text: string;
  error: string;
  metadata: Record<string, unknown>;
  created_at: string;
  sentinel_action: string | null;
  sentinel_suppressed: boolean;
}

export interface MessageDeliveryGroup {
  target_chat_address: string;
  platform: string;
  target_type: string;
  target_id: string;
  count: number;
  last_created_at: string;
  last_source: string;
}

export interface MessageDeliveryGroupsResponse {
  groups: MessageDeliveryGroup[];
}

export interface MessageDeliveriesResponse {
  target_chat_address: string;
  deliveries: MessageDelivery[];
}

export interface SessionSearchResult {
  result_type: "turn" | "delivery";
  id: string;
  session_id: string;
  target_chat_address: string;
  role: string;
  source: string;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
  sentinel_action: string | null;
  sentinel_suppressed: boolean;
  delivery_mode: string;
  status: string;
  message_id: string;
}

export interface SessionSearchResponse {
  results: SessionSearchResult[];
}

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  mtime: string;
}

export interface FileListResponse {
  workspace_id: string;
  path: string;
  entries: FileEntry[];
}

export interface FileContentResponse {
  workspace_id: string;
  path: string;
  content: string;
  size: number;
  mtime: string;
}

export interface WorkspaceInfo {
  workspace_id: string;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
  last_active_at: string;
}

export interface WorkspaceListResponse {
  workspaces: WorkspaceInfo[];
  active: string;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  logger: string;
  event: string;
  fields: Record<string, unknown>;
}

export interface LogsResponse {
  entries: LogEntry[];
}

// -- Plugins --

export type PluginState =
  | "found"
  | "loaded"
  | "enabled"
  | "disabled"
  | "error"
  | "unloaded";

export type PluginAction =
  | "load"
  | "enable"
  | "disable"
  | "reload"
  | "unload";

export interface PluginPermissions {
  network?: {
    outbound?: string[];
    inbound?: boolean;
  };
  filesystem?: {
    read?: string[];
    write?: string[];
  };
  memory?: {
    read?: boolean;
    write?: boolean;
  };
  system?: {
    env_vars?: string[];
    subprocess?: boolean;
    signal_handlers?: boolean;
  };
  llm_access?: boolean;
}

export interface PluginCapabilities {
  tools?: Record<string, string>[];
  subscribes_to?: string[];
}

export interface PluginDependency {
  id: string;
  version: string;
}

export interface PluginSummary {
  id: string;
  name: string;
  version: string;
  description: string;
  state: PluginState;
  path: string;
  entrypoint: string;
  load_phase: "pre-agent" | "post-agent" | string;
  nahida_bot_version: string;
  sdk_version: string;
  error_message: string;
  permissions: PluginPermissions;
  capabilities: PluginCapabilities;
  depends_on: PluginDependency[];
  config_keys: string[];
  config_schema: Record<string, unknown>;
  has_config: boolean;
  has_instance: boolean;
  has_runtime_api: boolean;
}

export interface PluginListResponse {
  plugins: PluginSummary[];
}

export interface PluginActionResponse {
  plugin_id: string;
  action: PluginAction;
  state: PluginState;
  status: string;
}

// -- Request / mutation types --

export interface ConfigSaveRequest {
  content: string;
  expected_checksum: string;
  format: string;
}

export interface ConfigPatchChange {
  path: string;
  value?: unknown;
  remove?: boolean;
  secret_action?: "keep" | "replace" | "clear";
}

export interface ConfigPatchRequest {
  expected_checksum: string;
  changes: ConfigPatchChange[];
}

export interface ConfigSaveResponse {
  saved: boolean;
  backup_path: string | null;
  checksum: string;
  restart_required: boolean;
  validation: {
    errors: number;
    warnings: number;
    issues: { severity: string; message: string; path: string }[];
  };
}

export interface SystemActionRequest {
  confirm: boolean;
  reason: string;
}

export interface SystemActionResponse {
  accepted: boolean;
  action: string;
  mode: string;
  message: string;
}

export interface CreateCronRequest {
  target: string;
  prompt: string;
  mode: "once" | "interval" | "cron";
  fire_at?: string | null;
  interval_seconds?: number | null;
  cron_expression?: string | null;
  max_runs?: number | null;
  session_mode: "main" | "isolated" | "fresh" | "named";
  session_name?: string | null;
}

export interface CreateCronResponse {
  job_id: string;
  status: string;
}

export interface UpdateCronRequest {
  prompt?: string | null;
  mode?: "once" | "interval" | "cron" | null;
  fire_at?: string | null;
  interval_seconds?: number | null;
  cron_expression?: string | null;
  max_runs?: number | null;
  session_mode?: "main" | "isolated" | "fresh" | "named" | null;
  session_name?: string | null;
}

export interface CronActionResponse {
  job_id: string;
  status: string;
}

export interface FileWriteRequest {
  path: string;
  content: string;
  workspace_id?: string | null;
}

export interface FileWriteResponse {
  path: string;
  size: number;
  mtime: string;
}

export interface FileUploadRequest {
  path: string;
  file: File;
  workspace_id?: string | null;
  overwrite?: boolean;
}

export interface FileUploadResponse {
  path: string;
  size: number;
  mtime: string;
}

export interface FileCreateRequest {
  path: string;
  content: string;
  workspace_id?: string | null;
}

export interface FileRenameRequest {
  path: string;
  new_name: string;
  workspace_id?: string | null;
}

export interface FileRenameResponse {
  path: string;
}

export interface FileDeleteRequest {
  path: string;
  workspace_id?: string | null;
}

export interface FileDeleteResponse {
  status: string;
  trash_path: string;
}

// -- Token Usage --

export interface TokenTotals {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cache_creation_tokens: number;
  estimated_cost: number | null;
  event_count: number;
}

export interface ProviderToken {
  provider_id: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cache_creation_tokens: number;
  estimated: boolean;
  estimated_cost: number | null;
  event_count: number;
}

export interface DailyToken {
  date: string;
  input_tokens: number;
  output_tokens: number;
  provider_id: string;
  model: string;
}

export interface TokenStatsResponse {
  totals: TokenTotals;
  by_provider: ProviderToken[];
  daily: DailyToken[];
}

export interface TokenEvent {
  id: number | null;
  timestamp: string;
  session_id: string;
  source_tag: string;
  provider_id: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cache_creation_tokens: number;
  estimated: boolean;
  estimated_cost: number | null;
}

export interface TokenEventsResponse {
  events: TokenEvent[];
}

export interface TokenClearResponse {
  cleared: boolean;
}
