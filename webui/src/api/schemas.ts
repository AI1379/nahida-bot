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
  auth: { required: boolean; mode: string };
  features: { id: string; route: string; label: string; scope: string }[];
  server_time: string;
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
  next_fire_at: string;
  run_count: number;
  created_at: string;
  session_mode: string;
  session_name: string | null;
  session_key: string;
  chat_type: string;
  last_fired_at: string | null;
  failure_count: number;
  last_error: string | null;
  workspace_id: string | null;
  fire_at: string | null;
  interval_seconds: number | null;
  cron_expression: string | null;
  max_runs: number | null;
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
}

export interface SessionHistoryResponse {
  session_id: string;
  turns: Turn[];
}

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: string;
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
  modified: string;
}

export interface WorkspaceInfo {
  id: string;
  name: string;
  path: string;
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

// -- Request / mutation types --

export interface ConfigSaveRequest {
  content: string;
  expected_checksum: string;
  format: string;
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
  session_mode: "main" | "isolated" | "named";
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
