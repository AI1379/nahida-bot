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
 * The domain state includes tokens while the app is running, but persistence
 * is split deliberately: ordinary settings use the Tauri Store plugin and
 * secrets use the platform credential store through Rust commands.
 */

export type GatewayConnectionMode = "mock" | "gateway";
export type TtsSourcePreference = "system" | "gateway" | "auto";
export type GatewayConnectionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "auth-required";

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
  /**
   * Optional admin API token (`webapi.auth_token`). Reused for REST calls
   * that require admin auth (e.g. /api/speech/jobs). Empty on no-auth
   * gateways. Stored in the platform credential store so the desktop can keep
   * using TTS without re-entering it each session.
   */
  adminBearerToken: string;
  /** Which TTS path to use when both system and gateway are available. */
  ttsSource: TtsSourcePreference;
}

export const defaultGatewayConnectionSettings: GatewayConnectionSettings = {
  mode: "gateway",
  gatewayWsUrl: "ws://127.0.0.1:6185/api/nodes/ws",
  nodeId: "desktop-local",
  displayName: "Nahida Desktop",
  defaultSessionId: "",
  nodeToken: "",
  adminBearerToken: "",
  ttsSource: "auto",
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
  return value === "mock" || value === "gateway"
    ? value
    : defaultGatewayConnectionSettings.mode;
}

function cleanTtsSource(value: unknown): TtsSourcePreference {
  return value === "system" || value === "gateway" || value === "auto"
    ? value
    : "auto";
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
  const adminBearerToken = cleanString(
    value.adminBearerToken,
    gatewayConnectionPolicy.maximumTokenLength,
  );

  return {
    mode: cleanMode(value.mode),
    gatewayWsUrl,
    nodeId,
    displayName,
    defaultSessionId,
    nodeToken,
    adminBearerToken,
    ttsSource: cleanTtsSource(value.ttsSource),
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

export function isGatewayConnectionConfigured(
  settings: GatewayConnectionSettings,
): boolean {
  return (
    settings.mode === "gateway" &&
    Boolean(sanitizeGatewayWsUrl(settings.gatewayWsUrl)) &&
    Boolean(settings.nodeId.trim()) &&
    Boolean(settings.displayName.trim()) &&
    isNodeToken(settings.nodeToken)
  );
}

export function isGatewayAuthError(reason: unknown): boolean {
  if (typeof reason !== "string") return false;
  return /(?:\b(?:401|403|unauthori[sz]ed|forbidden|authentication failed)\b|invalid[^\n]*token|token[^\n]*(?:invalid|expired|required)|expired[^\n]*token|missing[^\n]*token)/i.test(
    reason,
  );
}
