export type PluginLifecycleState =
  | "found"
  | "loaded"
  | "enabled"
  | "disabled"
  | "error"
  | "unloaded";

export type DesktopPluginRuntimeMode =
  | "builtin"
  | "javascript"
  | "wasm"
  | "sidecar";

export interface RemoteDesktopRuntimeFacet {
  entrypoint: string;
  mode: DesktopPluginRuntimeMode;
}

export interface RemotePluginPage {
  id: string;
  target: "webui.admin" | "desktop.main" | "desktop.popup";
  entry: string;
  title: string;
}

export interface RemotePluginRuntime {
  id: string;
  name: string;
  version: string;
  state: PluginLifecycleState;
  configuredEnabled: boolean;
  desktop: RemoteDesktopRuntimeFacet | null;
  pages: RemotePluginPage[];
}

export interface PluginRuntimeSnapshot {
  generation: string;
  revision: number;
  plugins: RemotePluginRuntime[];
}

const pluginStates = new Set<PluginLifecycleState>([
  "found",
  "loaded",
  "enabled",
  "disabled",
  "error",
  "unloaded",
]);
const desktopModes = new Set<DesktopPluginRuntimeMode>([
  "builtin",
  "javascript",
  "wasm",
  "sidecar",
]);
const pageTargets = new Set<RemotePluginPage["target"]>([
  "webui.admin",
  "desktop.main",
  "desktop.popup",
]);
const pluginIdPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const contributionIdPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const generationPattern = /^[a-f0-9]{32}$/;

export function parsePluginRuntimeSnapshot(
  value: unknown,
): PluginRuntimeSnapshot | null {
  if (!isRecord(value)) return null;
  const generation = readString(value.generation, 32);
  if (!generation || !generationPattern.test(generation)) return null;
  const revision = value.revision;
  if (!Number.isSafeInteger(revision) || Number(revision) < 1) return null;
  if (!Array.isArray(value.plugins) || value.plugins.length > 256) return null;

  const plugins: RemotePluginRuntime[] = [];
  const ids = new Set<string>();
  for (const item of value.plugins) {
    const plugin = parsePlugin(item);
    if (!plugin || ids.has(plugin.id)) return null;
    ids.add(plugin.id);
    plugins.push(plugin);
  }
  return { generation, revision: Number(revision), plugins };
}

function parsePlugin(value: unknown): RemotePluginRuntime | null {
  if (!isRecord(value)) return null;
  const id = readString(value.id, 128);
  const name = readString(value.name, 128);
  const version = readString(value.version, 64);
  if (!id || !pluginIdPattern.test(id) || !name || !version) return null;
  if (!pluginStates.has(value.state as PluginLifecycleState)) return null;
  if (typeof value.configured_enabled !== "boolean") return null;

  const runtimes = isRecord(value.runtimes) ? value.runtimes : {};
  const desktop = parseDesktopFacet(runtimes.desktop);
  if (runtimes.desktop != null && !desktop) return null;
  const contributes = isRecord(value.contributes) ? value.contributes : {};
  const pages = parsePages(contributes.pages);
  if (!pages) return null;
  return {
    id,
    name,
    version,
    state: value.state as PluginLifecycleState,
    configuredEnabled: value.configured_enabled,
    desktop,
    pages,
  };
}

function parseDesktopFacet(value: unknown): RemoteDesktopRuntimeFacet | null {
  if (value == null) return null;
  if (!isRecord(value)) return null;
  const entrypoint = readString(value.entrypoint, 256);
  if (!entrypoint || !desktopModes.has(value.mode as DesktopPluginRuntimeMode)) {
    return null;
  }
  return { entrypoint, mode: value.mode as DesktopPluginRuntimeMode };
}

function parsePages(value: unknown): RemotePluginPage[] | null {
  if (value == null) return [];
  if (!Array.isArray(value) || value.length > 32) return null;
  const pages: RemotePluginPage[] = [];
  const ids = new Set<string>();
  for (const item of value) {
    if (!isRecord(item)) return null;
    const id = readString(item.id, 64);
    const entry = readString(item.entry, 256);
    const target = item.target as RemotePluginPage["target"];
    if (
      !id ||
      !contributionIdPattern.test(id) ||
      ids.has(id) ||
      !entry ||
      !pageTargets.has(target)
    ) {
      return null;
    }
    ids.add(id);
    pages.push({
      id,
      target,
      entry,
      title: readString(item.title, 128) ?? "",
    });
  }
  return pages;
}

function readString(value: unknown, maximum: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized && normalized.length <= maximum ? normalized : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
