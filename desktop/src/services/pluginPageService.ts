import {
  gatewayWsUrlToHttpBase,
  type GatewayConnectionSettings,
} from "@/domain/gatewayConnection";
import type { ActiveRemotePluginPage } from "@/plugins/desktopPluginContract";

export interface PluginPageDocument {
  plugin_id: string;
  plugin_name: string;
  page_id: string;
  target: "webui.admin" | "desktop.main" | "desktop.popup";
  title: string;
  html: string;
}

const maximumPluginPageBytes = 1024 * 1024;

export async function fetchDesktopPluginPage(
  connection: GatewayConnectionSettings,
  activePage: ActiveRemotePluginPage,
  signal?: AbortSignal,
): Promise<PluginPageDocument> {
  const httpBase = gatewayWsUrlToHttpBase(connection.gatewayWsUrl);
  if (!httpBase) throw new Error("Gateway URL is not configured.");

  const headers = new Headers({ Accept: "application/json" });
  const bearer = connection.adminBearerToken.trim();
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  const response = await fetch(
    `${httpBase}/api/plugins/${encodeURIComponent(activePage.pluginId)}/pages/${encodeURIComponent(activePage.page.id)}`,
    { method: "GET", headers, signal },
  );
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(readApiError(payload, response.status));
  const document = parsePluginPageDocument(payload);
  if (!document) throw new Error("Gateway returned an invalid plugin page.");
  if (
    document.plugin_id !== activePage.pluginId ||
    document.page_id !== activePage.page.id ||
    document.target !== "desktop.main"
  ) {
    throw new Error("Gateway returned a plugin page for a different target.");
  }
  if (new TextEncoder().encode(document.html).byteLength > maximumPluginPageBytes) {
    throw new Error("Plugin page exceeds the 1 MiB limit.");
  }
  return document;
}

export function sandboxPluginPageDocument(page: PluginPageDocument): string {
  const context = JSON.stringify({
    version: 1,
    plugin: { id: page.plugin_id, name: page.plugin_name },
    page: { id: page.page_id, target: page.target, title: page.title },
  }).replaceAll("<", "\\u003c");
  const head = `
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; font-src data:; media-src data:; object-src 'none'; base-uri 'none'; form-action 'none'">
    <meta name="referrer" content="no-referrer">
    <script>(()=>{const value=${context};Object.freeze(value.plugin);Object.freeze(value.page);window.__NAHIDA_PLUGIN_CONTEXT__=Object.freeze(value)})()<\/script>
  `;
  if (/<head(?:\s[^>]*)?>/i.test(page.html)) {
    return page.html.replace(/<head(?:\s[^>]*)?>/i, (match) => `${match}${head}`);
  }
  return `<!doctype html><html><head>${head}</head><body>${page.html}</body></html>`;
}

function parsePluginPageDocument(value: unknown): PluginPageDocument | null {
  if (!isRecord(value)) return null;
  const pluginId = readString(value.plugin_id, 128);
  const pluginName = readString(value.plugin_name, 128);
  const pageId = readString(value.page_id, 64);
  const title = readString(value.title, 128, true);
  const html = typeof value.html === "string" ? value.html : null;
  const target = value.target;
  if (
    !pluginId ||
    !pluginName ||
    !pageId ||
    title === null ||
    html === null ||
    (target !== "webui.admin" &&
      target !== "desktop.main" &&
      target !== "desktop.popup")
  ) {
    return null;
  }
  return {
    plugin_id: pluginId,
    plugin_name: pluginName,
    page_id: pageId,
    target,
    title,
    html,
  };
}

function readApiError(value: unknown, status: number): string {
  if (isRecord(value) && typeof value.detail === "string" && value.detail.trim()) {
    return value.detail.trim();
  }
  return `Plugin page request failed (${status}).`;
}

function readString(
  value: unknown,
  maximum: number,
  allowEmpty = false,
): string | null {
  if (typeof value !== "string" || value.length > maximum) return null;
  const normalized = value.trim();
  return normalized || (allowEmpty ? "" : null);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
