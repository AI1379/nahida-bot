import {
  defaultGatewayConnectionSettings,
  sanitizeGatewayConnectionSettings,
} from "@/domain/gatewayConnection";
import type { GatewayConnectionSettings } from "@/domain/gatewayConnection";

const storageKey = "nahida.desktop.gateway.connection.v1";

export function sanitizeGatewayConnection(
  value: unknown,
): GatewayConnectionSettings {
  return sanitizeGatewayConnectionSettings(value);
}

export function readPersistedGatewayConnection(): GatewayConnectionSettings {
  if (typeof window === "undefined") {
    return sanitizeGatewayConnection(null);
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    return sanitizeGatewayConnection(raw ? JSON.parse(raw) : null);
  } catch {
    return sanitizeGatewayConnection(null);
  }
}

export function writePersistedGatewayConnection(
  settings: GatewayConnectionSettings,
): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    storageKey,
    JSON.stringify(sanitizeGatewayConnection(settings)),
  );
}

export function clearPersistedGatewayConnection(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(storageKey);
}

export function defaultGatewayConnection(): GatewayConnectionSettings {
  return { ...defaultGatewayConnectionSettings };
}
