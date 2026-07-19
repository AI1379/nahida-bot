import {
  gatewayWsUrlToHttpBase,
  sanitizeGatewayWsUrl,
} from "@/domain/gatewayConnection";

export interface PairingCompleteSuccess {
  ok: true;
  nodeToken: string;
  tokenId: string;
  nodeId: string;
  actorAccountKey?: string;
  conversationId?: string;
}

export interface PairingCompleteFailure {
  ok: false;
  error: string;
}

export interface PairingStartSuccess {
  pairingToken: string;
  tokenId: string;
  expiresInSeconds: number;
  nodeId: string;
}

export type PairingCompleteResult = PairingCompleteSuccess | PairingCompleteFailure;

export type GatewayAuthMode = "none" | "bearer" | "password";

export interface GatewayBootstrap {
  ok: true;
  appName: string;
  version: string;
  authRequired: boolean;
  authMode: GatewayAuthMode;
  apiTokenSupported: boolean;
  passwordConfigured: boolean;
}

export interface PairDeviceOptions {
  gatewayWsUrl: string;
  nodeId: string;
  displayName?: string;
  /**
   * Admin bearer token (typically `webapi.auth_token` from `config.yaml`).
   * Required when the gateway has auth enabled; omitted when bootstrap
   * reports `authRequired: false`.
   */
  adminBearerToken?: string;
}

export interface PairDeviceSuccess {
  ok: true;
  bootstrap: GatewayBootstrap;
  nodeToken: string;
  nodeId: string;
  conversationId?: string;
  /** The admin bearer was used to mint the pairing token; not persisted. */
  usedAdminBearer: boolean;
}

export type PairDeviceResult =
  | PairDeviceSuccess
  | (Omit<PairingCompleteFailure, "ok"> & { ok: false; bootstrap?: GatewayBootstrap });

interface RawPairingCompleteResponse {
  node_token?: unknown;
  token_id?: unknown;
  node_id?: unknown;
  actor_account_key?: unknown;
  conversation_id?: unknown;
}

interface RawPairingStartResponse {
  pairing_token?: unknown;
  token_id?: unknown;
  expires_in_seconds?: unknown;
  node_id?: unknown;
}

function readString(value: unknown, maxLength = 512): string {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, maxLength);
}

function readNonEmptyString(value: unknown, maxLength = 512): string | null {
  const candidate = readString(value, maxLength);
  return candidate || null;
}

/**
 * Resolve the HTTP base URL (scheme swapped from ws/wss to http/https) used
 * for REST pairing calls. Returns null if the WebSocket URL is invalid.
 */
export function resolveGatewayHttpBase(gatewayWsUrl: string): string | null {
  const cleaned = sanitizeGatewayWsUrl(gatewayWsUrl);
  if (!cleaned) return null;
  return gatewayWsUrlToHttpBase(cleaned) || null;
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function describeHttpError(status: number, fallback: string): string {
  if (status === 400) {
    return "Pairing token is invalid, expired or already used.";
  }
  if (status === 401 || status === 403) {
    return "Gateway rejected the request credentials.";
  }
  if (status === 404) {
    return "Gateway does not expose the pairing endpoint; check the URL.";
  }
  if (status >= 500) {
    return "Gateway returned a server error; try again.";
  }
  return fallback;
}

/**
 * Exchange a one-shot pairing token for a long-lived node token by calling
 * `POST /api/nodes/pairing/complete` on the Gateway. The pairing token is
 * consumed on success and must not be reused.
 */
export async function completeGatewayPairing(
  gatewayWsUrl: string,
  pairingToken: string,
): Promise<PairingCompleteResult> {
  const trimmedToken = pairingToken.trim();
  if (!trimmedToken) {
    return { ok: false, error: "Pairing token is empty." };
  }
  const httpBase = resolveGatewayHttpBase(gatewayWsUrl);
  if (!httpBase) {
    return {
      ok: false,
      error: "Gateway URL is invalid. Use a ws:// or wss:// URL.",
    };
  }

  let response: Response;
  try {
    response = await fetch(`${httpBase}/api/nodes/pairing/complete`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ pairing_token: trimmedToken }),
    });
  } catch (error) {
    return {
      ok: false,
      error: `Network error reaching gateway: ${String(error)}`,
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      error: describeHttpError(
        response.status,
        `Gateway returned HTTP ${response.status}.`,
      ),
    };
  }

  const payload = (await parseJson(response)) as RawPairingCompleteResponse | null;
  const nodeToken = readNonEmptyString(payload?.node_token);
  if (!nodeToken) {
    return {
      ok: false,
      error: "Gateway response did not include a node token.",
    };
  }

  return {
    ok: true,
    nodeToken,
    tokenId: readString(payload?.token_id, 128),
    nodeId: readString(payload?.node_id, 128),
    actorAccountKey: readNonEmptyString(payload?.actor_account_key, 256) ?? undefined,
    conversationId: readNonEmptyString(payload?.conversation_id, 256) ?? undefined,
  };
}
/**
 * Request a fresh pairing token from the Gateway. Requires admin auth on the
 * caller (WebUI session/bearer); desktop typically only uses this when the
 * operator is already authenticated.
 */
export async function requestGatewayPairingToken(
  gatewayWsUrl: string,
  options: {
    nodeId: string;
    displayName?: string;
    bearerToken?: string;
  },
): Promise<{ ok: true; result: PairingStartSuccess } | { ok: false; error: string }> {
  const httpBase = resolveGatewayHttpBase(gatewayWsUrl);
  if (!httpBase) {
    return { ok: false, error: "Gateway URL is invalid." };
  }
  const trimmedNodeId = options.nodeId.trim();
  if (!trimmedNodeId) {
    return { ok: false, error: "Node ID is required." };
  }

  const headers: Record<string, string> = {
    "content-type": "application/json",
  };
  if (options.bearerToken) {
    headers.authorization = `Bearer ${options.bearerToken.trim()}`;
  }

  let response: Response;
  try {
    response = await fetch(`${httpBase}/api/nodes/pairing/start`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        node_id: trimmedNodeId,
        display_name: options.displayName?.trim() ?? "",
      }),
    });
  } catch (error) {
    return { ok: false, error: `Network error: ${String(error)}` };
  }

  if (!response.ok) {
    return {
      ok: false,
      error: describeHttpError(
        response.status,
        `Gateway returned HTTP ${response.status}.`,
      ),
    };
  }

  const payload = (await parseJson(response)) as RawPairingStartResponse | null;
  const pairingToken = readNonEmptyString(payload?.pairing_token);
  if (!pairingToken) {
    return { ok: false, error: "Gateway did not return a pairing token." };
  }
  return {
    ok: true,
    result: {
      pairingToken,
      tokenId: readString(payload?.token_id, 128),
      expiresInSeconds:
        typeof payload?.expires_in_seconds === "number"
          ? payload.expires_in_seconds
          : 0,
      nodeId: readString(payload?.node_id, 128),
    },
  };
}

interface RawBootstrapAuth {
  required?: unknown;
  mode?: unknown;
  api_token_supported?: unknown;
  session_cookie?: unknown;
}

interface RawBootstrapResponse {
  app_name?: unknown;
  version?: unknown;
  auth?: unknown;
}

/**
 * Probe `/api/webui/bootstrap` (public) to learn whether the gateway needs
 * admin auth for protected endpoints. Lets the Desktop skip the bearer
 * prompt entirely on no-auth gateways (typical local dev / personal use).
 */
export async function fetchGatewayBootstrap(
  gatewayWsUrl: string,
): Promise<GatewayBootstrap | PairingCompleteFailure> {
  const httpBase = resolveGatewayHttpBase(gatewayWsUrl);
  if (!httpBase) {
    return {
      ok: false,
      error: "Gateway URL is invalid. Use a ws:// or wss:// URL.",
    };
  }

  let response: Response;
  try {
    response = await fetch(`${httpBase}/api/webui/bootstrap`, {
      method: "GET",
    });
  } catch (error) {
    return {
      ok: false,
      error: `Network error reaching gateway: ${String(error)}`,
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      error: describeHttpError(
        response.status,
        `Gateway returned HTTP ${response.status}.`,
      ),
    };
  }

  const payload = (await parseJson(response)) as RawBootstrapResponse | null;
  if (!payload || typeof payload !== "object") {
    return { ok: false, error: "Gateway returned an invalid bootstrap payload." };
  }

  const auth = (payload.auth ?? {}) as RawBootstrapAuth;
  const modeRaw = readString(auth.mode, 32);
  const mode: GatewayAuthMode =
    modeRaw === "password" || modeRaw === "bearer" || modeRaw === "none"
      ? modeRaw
      : "none";

  return {
    ok: true,
    appName: readString(payload.app_name, 256) || "Nahida Bot",
    version: readString(payload.version, 64),
    authRequired: Boolean(auth.required),
    authMode: mode,
    apiTokenSupported: Boolean(auth.api_token_supported),
    passwordConfigured: Boolean(auth.session_cookie),
  };
}

/**
 * Run the full self-pairing dance from the Desktop:
 *
 *  1. Bootstrap: figure out if the gateway needs admin auth.
 *  2. If auth required, require an admin bearer; if password-only, refuse
 *     with a helpful message (the user must set `webapi.auth_token` first).
 *  3. POST `/api/nodes/pairing/start` (with bearer if needed).
 *  4. POST `/api/nodes/pairing/complete` (public) to consume the pairing
 *     token and receive a long-lived node token.
 *
 * The intermediate pairing token never leaves this function. The admin
 * bearer is used only for the `pairing/start` call and is not returned.
 */
export async function pairDevice(
  options: PairDeviceOptions,
): Promise<PairDeviceResult> {
  const nodeId = options.nodeId.trim();
  if (!nodeId) {
    return { ok: false, error: "Node ID is required." };
  }

  const bootstrap = await fetchGatewayBootstrap(options.gatewayWsUrl);
  if (!bootstrap.ok) {
    return bootstrap;
  }

  const needsBearer =
    bootstrap.authRequired && bootstrap.apiTokenSupported;
  if (bootstrap.authRequired && !needsBearer) {
    return {
      ok: false,
      bootstrap,
      error:
        "Gateway requires WebUI admin password but no API token is configured. Set `webapi.auth_token` in config.yaml to enable desktop pairing.",
    };
  }

  if (needsBearer && !options.adminBearerToken?.trim()) {
    return {
      ok: false,
      bootstrap,
      error:
        "Gateway requires an admin API token. Paste the `webapi.auth_token` from config.yaml and try again.",
    };
  }

  const startResult = await requestGatewayPairingToken(
    options.gatewayWsUrl,
    {
      nodeId,
      displayName: options.displayName,
      bearerToken: needsBearer ? options.adminBearerToken : undefined,
    },
  );
  if (!startResult.ok) {
    return {
      ok: false,
      bootstrap,
      error: startResult.error,
    };
  }

  const completeResult = await completeGatewayPairing(
    options.gatewayWsUrl,
    startResult.result.pairingToken,
  );
  if (!completeResult.ok) {
    return {
      ok: false,
      bootstrap,
      error: completeResult.error,
    };
  }

  return {
    ok: true,
    bootstrap,
    nodeToken: completeResult.nodeToken,
    nodeId: completeResult.nodeId || nodeId,
    conversationId: completeResult.conversationId,
    usedAdminBearer: needsBearer,
  };
}
