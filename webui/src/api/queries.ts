import { useQuery, useMutation, useQueryClient } from "@tanstack/vue-query";
import { computed, type ComputedRef, type Ref } from "vue";
import { api, toApiError } from "./client";
import { useToastStore } from "@/stores/toast";
import { useAppStore } from "@/stores/app";
import type {
  BootstrapResponse,
  ConfigCurrentResponse,
  ConfigDocumentResponse,
  ConfigPatchRequest,
  ConfigSchemaResponse,
  ConfigSaveRequest,
  ConfigSaveResponse,
  CronJob,
  CronListResponse,
  CreateCronRequest,
  CreateCronResponse,
  CronActionResponse,
  FileContentResponse,
  FileCreateRequest,
  FileDeleteRequest,
  FileDeleteResponse,
  FileListResponse,
  FileRenameRequest,
  FileRenameResponse,
  FileUploadRequest,
  FileUploadResponse,
  FileWriteRequest,
  FileWriteResponse,
  LogsResponse,
  MessageDeliveriesResponse,
  MessageDeliveryGroupsResponse,
  PluginAction,
  PluginActionResponse,
  PluginListResponse,
  SessionHistoryResponse,
  SessionListResponse,
  SessionSearchResponse,
  StatusResponse,
  SystemActionRequest,
  SystemActionResponse,
  TokenStatsResponse,
  TokenClearResponse,
  TokenEventsResponse,
  UpdateCronRequest,
  WorkspaceListResponse,
  SkillListResponse,
  SkillDetailResponse,
  KbCollectionListResponse,
  KbSearchResponse,
  KbBatchImportResponse,
  KbImportResponse,
  KbActionResponse,
} from "./schemas";

export function useBootstrap() {
  return useQuery<BootstrapResponse>({
    queryKey: ["bootstrap"],
    queryFn: () => api.get("/webui/bootstrap"),
    staleTime: 5 * 60 * 1000,
  });
}

export function useStatus(pollMs: number = 0) {
  return useQuery<StatusResponse>({
    queryKey: ["status"],
    queryFn: () => api.get("/status"),
    refetchInterval: pollMs || undefined,
  });
}

export function useConfigCurrent() {
  return useQuery<ConfigCurrentResponse>({
    queryKey: ["config", "current"],
    queryFn: () => api.get("/config/current?redact=true"),
  });
}

export function useConfigDocument() {
  return useQuery<ConfigDocumentResponse>({
    queryKey: ["config", "document"],
    queryFn: () => api.get("/config/document"),
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

type ReadableRef<T> = Ref<T> | ComputedRef<T>;

export function useSessionHistory(sessionId: ReadableRef<string>) {
  return useQuery<SessionHistoryResponse>({
    queryKey: computed(() => ["sessions", sessionId.value]),
    queryFn: () => api.get(`/sessions/${sessionId.value}?limit=200`),
    enabled: computed(() => !!sessionId.value),
  });
}

export function useDeliveryGroups() {
  return useQuery<MessageDeliveryGroupsResponse>({
    queryKey: ["sessions", "delivery-groups"],
    queryFn: () => api.get("/sessions/delivery-groups?limit=500"),
  });
}

export function useMessageDeliveries(target: ReadableRef<string>) {
  return useQuery<MessageDeliveriesResponse>({
    queryKey: computed(() => ["sessions", "deliveries", target.value]),
    queryFn: () => {
      const params = new URLSearchParams({ target: target.value, limit: "200" });
      return api.get(`/sessions/deliveries?${params}`);
    },
    enabled: computed(() => !!target.value),
  });
}

export function useSessionSearch(
  params: ReadableRef<{ q: string; chat_address: string; source: string; role: string }>,
  enabled: ReadableRef<boolean>,
) {
  return useQuery<SessionSearchResponse>({
    queryKey: computed(() => ["sessions", "search", params.value]),
    queryFn: () => {
      const p = new URLSearchParams();
      if (params.value.q) p.set("q", params.value.q);
      if (params.value.chat_address) p.set("chat_address", params.value.chat_address);
      if (params.value.source) p.set("source", params.value.source);
      if (params.value.role) p.set("role", params.value.role);
      p.set("limit", "200");
      return api.get(`/sessions/search?${p}`);
    },
    enabled,
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
    refetchInterval: computed(() => (paused.value ? false : 10_000)),
  });
}

export function usePluginList() {
  return useQuery<PluginListResponse>({
    queryKey: ["plugins"],
    queryFn: () => api.get("/plugins"),
  });
}

// -- Mutations --

export function useConfigSave() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const app = useAppStore();

  return useMutation<ConfigSaveResponse, Error, ConfigSaveRequest>({
    mutationFn: (body) => api.put<ConfigSaveResponse>("/config/current", body),
    onSuccess(data) {
      qc.invalidateQueries({ queryKey: ["config", "current"] });
      if (data.restart_required) {
        app.setRestartRequired(data.backup_path);
      }
      toast.add("Configuration saved." + (data.backup_path ? ` Backup: ${data.backup_path}` : ""), "success");
    },
    onError(err) {
      const apiErr = toApiError(err);
      if (apiErr.status === 409) {
        toast.add("Config was modified externally. Re-reading...", "warning");
        qc.invalidateQueries({ queryKey: ["config", "current"] });
      } else {
        toast.add(`Save failed: ${apiErr.detail}`, "error");
      }
    },
  });
}

export function useConfigPatchSave() {
  const qc = useQueryClient();
  const toast = useToastStore();
  const app = useAppStore();

  return useMutation<ConfigSaveResponse, Error, ConfigPatchRequest>({
    mutationFn: (body) => api.patch<ConfigSaveResponse>("/config/current", body),
    onSuccess(data) {
      qc.invalidateQueries({ queryKey: ["config", "current"] });
      qc.invalidateQueries({ queryKey: ["config", "document"] });
      if (data.restart_required) {
        app.setRestartRequired(data.backup_path);
      }
      toast.add("Configuration saved." + (data.backup_path ? ` Backup: ${data.backup_path}` : ""), "success");
    },
    onError(err) {
      const apiErr = toApiError(err);
      if (apiErr.status === 409) {
        toast.add("Config was modified externally or validation failed. Re-reading...", "warning");
        qc.invalidateQueries({ queryKey: ["config", "document"] });
      } else {
        toast.add(`Save failed: ${apiErr.detail}`, "error");
      }
    },
  });
}

export function useSystemRestart() {
  const toast = useToastStore();

  return useMutation<SystemActionResponse, Error, string>({
    mutationFn: (reason: string) =>
      api.post<SystemActionResponse>("/system/actions/restart", {
        confirm: true,
        reason,
      } satisfies SystemActionRequest),
    onSuccess(data) {
      toast.add(data.message || "Restart requested. Server will shut down.", "success");
    },
    onError(err) {
      toast.add(`Restart failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useSystemShutdown() {
  const toast = useToastStore();

  return useMutation<SystemActionResponse, Error, string>({
    mutationFn: (reason: string) =>
      api.post<SystemActionResponse>("/system/actions/shutdown", {
        confirm: true,
        reason,
      } satisfies SystemActionRequest),
    onSuccess(data) {
      toast.add(data.message || "Shutdown requested. Process will exit.", "success");
    },
    onError(err) {
      toast.add(`Shutdown failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function usePluginAction() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<
    PluginActionResponse,
    Error,
    { pluginId: string; action: PluginAction }
  >({
    mutationFn: ({ pluginId, action }) =>
      api.post<PluginActionResponse>(
        `/plugins/${encodeURIComponent(pluginId)}/${action}`,
      ),
    onSuccess(data) {
      qc.invalidateQueries({ queryKey: ["plugins"] });
      toast.add(`Plugin ${data.action} completed.`, "success");
    },
    onError(err) {
      toast.add(`Plugin action failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useCronCreate() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<CreateCronResponse, Error, CreateCronRequest>({
    mutationFn: (body) => api.post<CreateCronResponse>("/cron", body),
    onSuccess() {
      qc.invalidateQueries({ queryKey: ["cron", "list"] });
      toast.add("CRON job created.", "success");
    },
    onError(err) {
      toast.add(`Create failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useCronUpdate() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<
    CronJob,
    Error,
    { jobId: string; data: UpdateCronRequest }
  >({
    mutationFn: ({ jobId, data }) =>
      api.patch<CronJob>(`/cron/${jobId}`, data),
    onSuccess() {
      qc.invalidateQueries({ queryKey: ["cron", "list"] });
      toast.add("CRON job updated.", "success");
    },
    onError(err) {
      toast.add(`Update failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useCronActivate() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<CronActionResponse, Error, string>({
    mutationFn: (jobId) =>
      api.post<CronActionResponse>(`/cron/${jobId}/activate`),
    onSuccess() {
      qc.invalidateQueries({ queryKey: ["cron", "list"] });
      toast.add("CRON job activated.", "success");
    },
    onError(err) {
      toast.add(`Activate failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useCronCancel() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<CronActionResponse, Error, string>({
    mutationFn: (jobId) =>
      api.post<CronActionResponse>(`/cron/${jobId}/cancel`),
    onSuccess() {
      qc.invalidateQueries({ queryKey: ["cron", "list"] });
      toast.add("CRON job cancelled.", "success");
    },
    onError(err) {
      toast.add(`Cancel failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useCronDelete() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<CronActionResponse, Error, string>({
    mutationFn: (jobId) =>
      api.del<CronActionResponse>(`/cron/${jobId}`),
    onSuccess() {
      qc.invalidateQueries({ queryKey: ["cron", "list"] });
      toast.add("CRON job deleted.", "success");
    },
    onError(err) {
      toast.add(`Delete failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useFileSave() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<FileWriteResponse, Error, FileWriteRequest>({
    mutationFn: (body) => api.put<FileWriteResponse>("/files/content", body),
    onSuccess(_data, variables) {
      qc.invalidateQueries({
        queryKey: ["files", "content", variables.workspace_id ?? "default", variables.path],
      });
      qc.invalidateQueries({
        queryKey: ["files", variables.workspace_id ?? "default"],
      });
      toast.add("File saved.", "success");
    },
    onError(err) {
      toast.add(`Save failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useFileCreate() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<FileWriteResponse, Error, FileCreateRequest>({
    mutationFn: (body) => api.post<FileWriteResponse>("/files/create", body),
    onSuccess(_data, variables) {
      qc.invalidateQueries({
        queryKey: ["files", variables.workspace_id ?? "default"],
      });
      toast.add("File created.", "success");
    },
    onError(err) {
      toast.add(`Create failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useFileUpload() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<FileUploadResponse, Error, FileUploadRequest>({
    mutationFn: (body) => {
      const form = new FormData();
      form.set("path", body.path);
      form.set("file", body.file);
      if (body.workspace_id) form.set("workspace_id", body.workspace_id);
      form.set("overwrite", body.overwrite ? "true" : "false");
      return api.postForm<FileUploadResponse>("/files/upload", form);
    },
    onSuccess(_data, variables) {
      const ws = variables.workspace_id ?? "default";
      qc.invalidateQueries({ queryKey: ["files", ws] });
      qc.invalidateQueries({ queryKey: ["files", "content", ws] });
      toast.add("File uploaded.", "success");
    },
    onError(err) {
      toast.add(`Upload failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useFileRename() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<FileRenameResponse, Error, FileRenameRequest>({
    mutationFn: (body) => api.post<FileRenameResponse>("/files/rename", body),
    onSuccess(_data, variables) {
      const ws = variables.workspace_id ?? "default";
      qc.invalidateQueries({ queryKey: ["files", ws] });
      qc.invalidateQueries({ queryKey: ["files", "content", ws] });
      toast.add("File renamed.", "success");
    },
    onError(err) {
      toast.add(`Rename failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useFileDelete() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<FileDeleteResponse, Error, FileDeleteRequest>({
    mutationFn: (body) => api.post<FileDeleteResponse>("/files/delete", body),
    onSuccess(_data, variables) {
      const ws = variables.workspace_id ?? "default";
      qc.invalidateQueries({ queryKey: ["files", ws] });
      qc.invalidateQueries({ queryKey: ["files", "content", ws] });
      toast.add("File deleted.", "success");
    },
    onError(err) {
      toast.add(`Delete failed: ${toApiError(err).detail}`, "error");
    },
  });
}

// -- Token Usage Queries --

export function useTokenStats(params?: Ref<{ provider_id?: string; days?: number }>) {
  return useQuery<TokenStatsResponse>({
    queryKey: computed(() => ["tokens", "stats", params?.value]),
    queryFn: () => {
      const p = new URLSearchParams();
      const v = params?.value;
      if (v?.provider_id) p.set("provider_id", v.provider_id);
      if (v?.days) p.set("days", String(v.days));
      else p.set("days", "7");
      return api.get(`/tokens/stats?${p}`);
    },
  });
}

export function useTokenEvents(params?: Ref<{ provider_id?: string; limit?: number }>) {
  return useQuery<TokenEventsResponse>({
    queryKey: computed(() => ["tokens", "events", params?.value]),
    queryFn: () => {
      const p = new URLSearchParams();
      const v = params?.value;
      if (v?.provider_id) p.set("provider_id", v.provider_id);
      p.set("limit", String(v?.limit ?? 100));
      return api.get(`/tokens/events?${p}`);
    },
  });
}

export function useTokenClear() {
  const qc = useQueryClient();
  const toast = useToastStore();

  return useMutation<TokenClearResponse, Error, void>({
    mutationFn: () => api.del<TokenClearResponse>("/tokens?confirm=true"),
    onSuccess(data) {
      qc.invalidateQueries({ queryKey: ["tokens"] });
      if (data.cleared) {
        toast.add("Token usage history cleared.", "success");
      }
    },
    onError(err) {
      toast.add(`Clear failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useSkills() {
  return useQuery<SkillListResponse>({
    queryKey: ["skills"],
    queryFn: () => api.get<SkillListResponse>("/skills"),
    staleTime: 30_000,
  });
}

export function useSkillContent(name: Ref<string | null>) {
  return useQuery<SkillDetailResponse | null>({
    queryKey: ["skills", name],
    queryFn: () => {
      if (!name.value) return null;
      return api.get<SkillDetailResponse>(`/skills/${encodeURIComponent(name.value)}`);
    },
    staleTime: 30_000,
    enabled: computed(() => !!name.value),
  });
}

// ── Knowledge Base ──────────────────────────────────

export function useKbCollections() {
  return useQuery<KbCollectionListResponse>({
    queryKey: ["kb", "collections"],
    queryFn: () => api.get<KbCollectionListResponse>("/kb/collections"),
    staleTime: 10_000,
  });
}

export function useKbCreateCollection() {
  const qc = useQueryClient();
  const toast = useToastStore();
  return useMutation<KbActionResponse, Error, string>({
    mutationFn: (name: string) =>
      api.post<KbActionResponse>(`/kb/collections/${encodeURIComponent(name)}/create`),
    onSuccess(_, name) {
      qc.invalidateQueries({ queryKey: ["kb", "collections"] });
      toast.add(`Collection "${name}" created.`, "success");
    },
    onError(err) {
      toast.add(`Create failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useKbDeleteCollection() {
  const qc = useQueryClient();
  const toast = useToastStore();
  return useMutation<KbActionResponse, Error, string>({
    mutationFn: (name: string) =>
      api.del<KbActionResponse>(`/kb/collections/${encodeURIComponent(name)}`),
    onSuccess(_, name) {
      qc.invalidateQueries({ queryKey: ["kb", "collections"] });
      toast.add(`Collection "${name}" deleted.`, "success");
    },
    onError(err) {
      toast.add(`Delete failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useKbImportText() {
  const qc = useQueryClient();
  const toast = useToastStore();
  return useMutation<
    KbImportResponse,
    Error,
    { collection: string; source: string; content: string; contentType?: string }
  >({
    mutationFn: ({ collection, source, content, contentType }) => {
      const form = new FormData();
      form.append("source", source);
      form.append("content", content);
      form.append("content_type", contentType || "text");
      return api.postForm<KbImportResponse>(
        `/kb/collections/${encodeURIComponent(collection)}/import-text`,
        form,
      );
    },
    onSuccess(resp) {
      qc.invalidateQueries({ queryKey: ["kb", "collections"] });
      toast.add(
        `Imported "${resp.source}" → ${resp.chunks} chunks in "${resp.collection}".`,
        "success",
      );
    },
    onError(err) {
      toast.add(`Import failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useKbImportFile() {
  const qc = useQueryClient();
  const toast = useToastStore();
  return useMutation<
    KbImportResponse,
    Error,
    { collection: string; file: File }
  >({
    mutationFn: ({ collection, file }) => {
      const form = new FormData();
      form.append("file", file);
      return api.postForm<KbImportResponse>(
        `/kb/collections/${encodeURIComponent(collection)}/import-file`,
        form,
      );
    },
    onSuccess(resp) {
      qc.invalidateQueries({ queryKey: ["kb", "collections"] });
      toast.add(
        `Imported "${resp.source}" → ${resp.chunks} chunks in "${resp.collection}".`,
        "success",
      );
    },
    onError(err) {
      toast.add(`Import failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useKbImportFiles() {
  const qc = useQueryClient();
  const toast = useToastStore();
  return useMutation<
    KbBatchImportResponse,
    Error,
    { collection: string; files: File[] }
  >({
    mutationFn: ({ collection, files }) => {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      return api.postForm<KbBatchImportResponse>(
        `/kb/collections/${encodeURIComponent(collection)}/import-files`,
        form,
      );
    },
    onSuccess(resp) {
      qc.invalidateQueries({ queryKey: ["kb", "collections"] });
      const summary = `${resp.imported_files} imported, ${resp.failed_files} failed, ${resp.chunks} chunks.`;
      toast.add(
        summary,
        resp.failed_files === 0
          ? "success"
          : resp.imported_files > 0
            ? "warning"
            : "error",
      );
    },
    onError(err) {
      toast.add(`Batch import failed: ${toApiError(err).detail}`, "error");
    },
  });
}

export function useKbSearch() {
  const toast = useToastStore();
  return useMutation<
    KbSearchResponse,
    Error,
    { collection: string; query: string; limit?: number }
  >({
    mutationFn: ({ collection, query, limit }) => {
      const form = new FormData();
      form.append("query", query);
      form.append("limit", String(limit || 5));
      return api.postForm<KbSearchResponse>(
        `/kb/collections/${encodeURIComponent(collection)}/search`,
        form,
      );
    },
    onError(err) {
      toast.add(`Search failed: ${toApiError(err).detail}`, "error");
    },
  });
}
