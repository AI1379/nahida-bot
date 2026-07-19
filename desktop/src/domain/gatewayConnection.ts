/**
 * Gateway connection settings for the Desktop app.
 *
 * The Desktop App can run in two modes:
 *  - `mock`: in-browser/dev mock backend, no Gateway required
 *  - `gateway`: real Gateway-Node WebSocket connection via Tauri
 *
 * Pairing model (V1):
 *  - The user obtains a node token out-of-band (admin WebUI, env var, etc.)
 *    and pastes it into the settings panel, OR
 *  - The user obtains a one-shot pairing token (`np_...`) and the Desktop
 *    exchanges it via `/api/nodes/pairing/complete` for a long-lived node
 *    token. The pairing token is consumed on success.
 *
 * Tokens persist to localStorage in V1 (see `services/gatewayConnectionStorage`);
 * a later Tauri build should move them to the platform keychain without
 * changing this domain shape.
 */

export type GatewayConnectionMode = "mock" | "gateway";

export const gatewayConnectionPolicy = {
  maximumUrlLength: 512,
  maximumNodeIdLength: 64,
  maximumDisplayNameLength: 96,
  maximumSessionIdLength: 128,
  maximumTokenLength: 512,
  allowedSchemes: new Set(["ws:", "wss:", "http:", "https:"]),
  /** Schemes permitted for the WebSocket URL we actually connect to. */
  allowedWsSchemes: new Set(["ws:", "wss:"]),
} as const;

export interface GatewayConnectionSettings {
  mode: GatewayConnectionMode;
  /** WebSocket URL for `/api/nodes/ws`, e.g. `ws://127.0.0.1:6185/api/nodes/ws`. */
  gatewayWsUrl: string;
  nodeId: string;
  displayName: string;
  /** Optional default session used when the gateway does not return one. */
  defaultSessionId: string;
  /** Long-lived node token (`nt_...`). Empty when not yet paired. */
  nodeToken: string;
}

export const defaultGatewayConnectionSettings: GatewayConnectionSettings = {
  mode: "mock",
  gatewayWsUrl: "ws://127.0.0.1:6185/api/nodes/ws",
  nodeId: "desktop-local",
  displayName: "Nahida Desktop",
  defaultSessionId: "",
  nodeToken: "",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function cleanString(value: unknown, maxLength: number): string {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, maxLength);
}

function cleanNodeId(value: unknown): string {
  const raw = cleanString(value, gatewayConnectionPolicy.maximumNodeIdLength);
  if (!raw) return "";
  // Node IDs become URL path segments and metadata keys; keep them tame.
  return /^[\p{L}\p{N}_.\-:]+$/u.test(raw) ? raw : "";
}

function cleanMode(value: unknown): GatewayConnectionMode {
  return value === "gateway" ? "gateway" : "mock";
}

export function sanitizeGatewayWsUrl(value: unknown): string {
  const raw = cleanString(value, gatewayConnectionPolicy.maximumUrlLength);
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    if (!gatewayConnectionPolicy.allowedWsSchemes.has(parsed.protocol)) {
      return "";
    }
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/**
 * Convert a WebSocket URL (`ws://host/api/nodes/ws`) to the HTTP base URL
 * (`http://host`) used for the REST pairing endpoints. Returns empty string
 * when the input is not a valid ws/wss URL.
 */
export function gatewayWsUrlToHttpBase(value: unknown): string {
  const cleaned = sanitizeGatewayWsUrl(value);
  if (!cleaned) return "";
  try {
    const parsed = new URL(cleaned);
    parsed.protocol = parsed.protocol === "wss:" ? "https:" : "http:";
    parsed.pathname = "/";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export function sanitizeGatewayConnectionSettings(
  value: unknown,
): GatewayConnectionSettings {
  if (!isRecord(value)) {
    return { ...defaultGatewayConnectionSettings };
  }
  const gatewayWsUrl =
    sanitizeGatewayWsUrl(value.gatewayWsUrl) ||
    defaultGatewayConnectionSettings.gatewayWsUrl;
  const nodeId =
    cleanNodeId(value.nodeId) || defaultGatewayConnectionSettings.nodeId;
  const displayName =
    cleanString(value.displayName, gatewayConnectionPolicy.maximumDisplayNameLength) ||
    defaultGatewayConnectionSettings.displayName;
  const defaultSessionId = cleanString(
    value.defaultSessionId,
    gatewayConnectionPolicy.maximumSessionIdLength,
  );
  const nodeToken = cleanString(
    value.nodeToken,
    gatewayConnectionPolicy.maximumTokenLength,
  );

  return {
    mode: cleanMode(value.mode),
    gatewayWsUrl,
    nodeId,
    displayName,
    defaultSessionId,
    nodeToken,
  };
}

export function isPairingToken(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > gatewayConnectionPolicy.maximumTokenLength) {
    return false;
  }
  return /^np[\w.-]*\.[\w.-]+$/.test(trimmed);
}

export function isNodeToken(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > gatewayConnectionPolicy.maximumTokenLength) {
    return false;
  }
  return /^nt[\w.-]*\.[\w.-]+$/.test(trimmed);
}
