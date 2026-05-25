import { useAuthStore } from "@/stores/auth";
import type { ApiError } from "./schemas";

const BASE = "/api";

class ClientError extends Error {
  status: number;
  code: string | undefined;
  detail: string;

  constructor(status: number, code: string | undefined, detail: string) {
    super(detail);
    this.name = "ClientError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ClientError) {
    return { status: err.status, code: err.code, detail: err.detail };
  }
  return { status: 0, detail: String(err) };
}

async function request<T>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const url = `${BASE}${path}`;
  const headers = new Headers(opts.headers);

  const auth = useAuthStore();
  if (auth.token) {
    headers.set("Authorization", `Bearer ${auth.token}`);
  }

  if (opts.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, { ...opts, headers });

  if (res.status === 401) {
    auth.clear();
    throw new ClientError(401, "unauthorized", "Authentication required");
  }

  if (!res.ok) {
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
      code = body.code;
    } catch {
      /* ignore parse error */
    }
    throw new ClientError(res.status, code, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),

  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),

  del: <T>(path: string) =>
    request<T>(path, { method: "DELETE" }),
};

export { toApiError };
export { ClientError };
