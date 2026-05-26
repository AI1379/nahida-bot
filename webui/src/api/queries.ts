import { useQuery } from "@tanstack/vue-query";
import { computed, type Ref } from "vue";
import { api } from "./client";
import type {
  BootstrapResponse,
  ConfigCurrentResponse,
  ConfigSchemaResponse,
  CronListResponse,
  FileContentResponse,
  FileListResponse,
  LogsResponse,
  SessionHistoryResponse,
  SessionListResponse,
  StatusResponse,
  WorkspaceListResponse,
} from "./schemas";

export function useBootstrap() {
  return useQuery<BootstrapResponse>({
    queryKey: ["bootstrap"],
    queryFn: () => api.get("/webui/bootstrap"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useStatus(pollMs: number = 10_000) {
  return useQuery<StatusResponse>({
    queryKey: ["status"],
    queryFn: () => api.get("/status"),
    refetchInterval: pollMs,
  });
}

export function useConfigCurrent() {
  return useQuery<ConfigCurrentResponse>({
    queryKey: ["config", "current"],
    queryFn: () => api.get("/config/current?redact=true"),
  });
}

export function useConfigSchema() {
  return useQuery<ConfigSchemaResponse>({
    queryKey: ["config", "schema"],
    queryFn: () => api.get("/config/schema?include_plugins=true"),
  });
}

export function useCronList(filter?: Ref<{ active?: string }>) {
  return useQuery<CronListResponse>({
    queryKey: computed(() => ["cron", "list", filter?.value]),
    queryFn: () => {
      const params = new URLSearchParams();
      const f = filter?.value;
      if (f?.active && f.active !== "all") params.set("active", f.active);
      params.set("limit", "200");
      return api.get(`/cron/jobs?${params}`);
    },
  });
}

export function useSessionList() {
  return useQuery<SessionListResponse>({
    queryKey: ["sessions"],
    queryFn: () => api.get("/sessions?limit=200"),
  });
}

export function useSessionHistory(sessionId: Ref<string>) {
  return useQuery<SessionHistoryResponse>({
    queryKey: computed(() => ["sessions", sessionId.value]),
    queryFn: () => api.get(`/sessions/${sessionId.value}?limit=200`),
    enabled: computed(() => !!sessionId.value),
  });
}

export function useWorkspaces() {
  return useQuery<WorkspaceListResponse>({
    queryKey: ["workspaces"],
    queryFn: () => api.get("/workspaces"),
  });
}

export function useFileList(
  workspaceId: Ref<string>,
  path: Ref<string>,
) {
  return useQuery<FileListResponse>({
    queryKey: computed(() => ["files", workspaceId.value, path.value]),
    queryFn: () => {
      const params = new URLSearchParams({
        workspace_id: workspaceId.value,
        path: path.value,
      });
      return api.get(`/files?${params}`);
    },
  });
}

export function useFileContent(
  workspaceId: Ref<string>,
  path: Ref<string>,
) {
  return useQuery<FileContentResponse>({
    queryKey: computed(() => ["files", "content", workspaceId.value, path.value]),
    queryFn: () => {
      const params = new URLSearchParams({
        workspace_id: workspaceId.value,
        path: path.value,
      });
      return api.get(`/files/content?${params}`);
    },
    enabled: computed(() => !!path.value),
  });
}

export function useLogs(
  params: Ref<{ level: string; logger: string; search: string }>,
  paused: Ref<boolean>,
) {
  return useQuery<LogsResponse>({
    queryKey: computed(() => ["logs", params.value]),
    queryFn: () => {
      const p = new URLSearchParams();
      if (params.value.level && params.value.level !== "ALL")
        p.set("level", params.value.level);
      if (params.value.logger) p.set("logger", params.value.logger);
      if (params.value.search) p.set("search", params.value.search);
      p.set("limit", "500");
      return api.get(`/logs?${p}`);
    },
    refetchInterval: computed(() => (paused.value ? false : 3000)),
  });
}
