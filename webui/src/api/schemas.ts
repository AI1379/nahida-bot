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
