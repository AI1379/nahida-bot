import { ref, onScopeDispose } from "vue";
import type { QueryClient } from "@tanstack/vue-query";
import { useAuthStore } from "@/stores/auth";
import type { LogEntry } from "./schemas";

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

function startConnection() {
  stopConnection();

  const auth = useAuthStore();
  if (!auth.token) return;

  const url = `/api/events/stream?token=${encodeURIComponent(auth.token)}`;
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
      startConnection();
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
  startConnection();

  onScopeDispose(() => {
    // Keep connection alive — this is an app-wide singleton.
    // Full cleanup happens on page unload.
  });

  return { connected };
}

export function setLogEntryHandler(handler: LogEntryHandler | null) {
  logEntryHandler = handler;
}

export { connected };
