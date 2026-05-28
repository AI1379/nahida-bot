import { ref, onScopeDispose } from "vue";
import type { QueryClient } from "@tanstack/vue-query";
import { useAuthStore } from "@/stores/auth";
import { api } from "./client";
import type { BootstrapResponse, LogEntry } from "./schemas";

export type SseEventType =
  | "status.updated"
  | "log.entry"
  | "message.received"
  | "message.sent"
  | "plugin.error"
  | "cron.fired"
  | "cron.failed"
  | "cron.updated"
  | "config.saved"
  | "ping";

export type LogEntryHandler = (entry: LogEntry) => void;

const connected = ref(false);
let es: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 1000;
let logEntryHandler: LogEntryHandler | null = null;
let queryClient: QueryClient | null = null;

function stopConnection() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (es !== null) {
    es.close();
    es = null;
  }
  connected.value = false;
}

function invalidateOnEvent(eventType: string) {
  if (!queryClient) return;
  switch (eventType) {
    case "status.updated":
      queryClient.invalidateQueries({ queryKey: ["status"] });
      break;
    case "message.received":
    case "message.sent":
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      break;
    case "cron.fired":
    case "cron.failed":
    case "cron.updated":
      queryClient.invalidateQueries({ queryKey: ["cron", "list"] });
      break;
    case "config.saved":
      queryClient.invalidateQueries({ queryKey: ["config", "current"] });
      break;
  }
}

async function loadBootstrap() {
  if (!queryClient) return api.get<BootstrapResponse>("/webui/bootstrap");
  return queryClient.fetchQuery<BootstrapResponse>({
    queryKey: ["bootstrap"],
    queryFn: () => api.get("/webui/bootstrap"),
    staleTime: 5 * 60 * 1000,
  });
}

async function startConnection() {
  stopConnection();

  const auth = useAuthStore();
  const bootstrap = await loadBootstrap();
  const requiresAuth = bootstrap.auth.required;
  const usesBearer = bootstrap.auth.mode === "bearer";
  if (requiresAuth && usesBearer && !auth.token) return;
  if (requiresAuth && !usesBearer && !auth.sessionAuthenticated) return;

  // EventSource cannot send Authorization headers. Bearer mode is retained for
  // scripts and legacy setups; browser password login uses the session cookie.
  const url = requiresAuth && usesBearer
    ? `/api/events/stream?token=${encodeURIComponent(auth.token)}`
    : "/api/events/stream";
  es = new EventSource(url);

  es.onopen = () => {
    connected.value = true;
    reconnectDelay = 1000;
  };

  es.onerror = () => {
    connected.value = false;
    es?.close();
    es = null;

    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, 30_000);
      void startConnection();
    }, reconnectDelay);
  };

  es.addEventListener("log.entry", (e: MessageEvent) => {
    try {
      const entry = JSON.parse(e.data) as LogEntry;
      logEntryHandler?.(entry);
    } catch {
      /* ignore parse errors */
    }
  });

  const eventTypes = [
    "status.updated",
    "message.received",
    "message.sent",
    "plugin.error",
    "cron.fired",
    "cron.failed",
    "cron.updated",
    "config.saved",
  ] as const;

  for (const type of eventTypes) {
    es.addEventListener(type, () => {
      invalidateOnEvent(type);
    });
  }
}

export function useEventStream(qc: QueryClient) {
  queryClient = qc;
  void startConnection();

  onScopeDispose(() => {
    // Keep connection alive — this is an app-wide singleton.
    // Full cleanup happens on page unload.
  });

  return { connected };
}

export function setLogEntryHandler(handler: LogEntryHandler | null) {
  logEntryHandler = handler;
}

export function disconnectEventStream() {
  stopConnection();
}

export { connected };
