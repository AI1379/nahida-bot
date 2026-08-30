export type PluginSurfaceTarget =
  | "desktop.home"
  | "desktop.sidebar"
  | "pet.overlay"
  | "pet.drawer";

export type PluginSurfaceKind =
  | "text"
  | "badge"
  | "countdown"
  | "progress"
  | "list"
  | "card";

export type PluginSurfaceTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

export interface PluginSurfaceItem {
  text: string;
  detail: string;
  completed: boolean;
}

export interface PluginSurfaceView {
  title: string;
  text: string;
  status: string;
  detail: string;
  expiresAt: string;
  progress: number | null;
  items: PluginSurfaceItem[];
  tone: PluginSurfaceTone;
}

export interface PluginSurfaceContribution {
  ownerPluginId: string;
  id: string;
  target: PluginSurfaceTarget;
  kind: PluginSurfaceKind;
  priority: number;
  source: "gateway" | "local";
  view: PluginSurfaceView;
}

export interface PluginSurfaceSnapshot {
  revision: number;
  surfaces: PluginSurfaceContribution[];
}

const targets = new Set<PluginSurfaceTarget>([
  "desktop.home",
  "desktop.sidebar",
  "pet.overlay",
  "pet.drawer",
]);
const kinds = new Set<PluginSurfaceKind>([
  "text",
  "badge",
  "countdown",
  "progress",
  "list",
  "card",
]);
const tones = new Set<PluginSurfaceTone>([
  "neutral",
  "info",
  "success",
  "warning",
  "danger",
]);
const identifierPattern = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

export function parseGatewayPluginSurfaceSnapshot(
  value: Record<string, unknown>,
): PluginSurfaceSnapshot | null {
  const revision = value.revision;
  const rawSurfaces = value.surfaces;
  if (
    typeof revision !== "number" ||
    !Number.isSafeInteger(revision) ||
    revision < 0 ||
    !Array.isArray(rawSurfaces) ||
    rawSurfaces.length > 100
  ) {
    return null;
  }

  const surfaces: PluginSurfaceContribution[] = [];
  const identities = new Set<string>();
  for (const raw of rawSurfaces) {
    const surface = parseSurface(raw);
    if (!surface) return null;
    const identity = `${surface.ownerPluginId}:${surface.id}`;
    if (identities.has(identity)) return null;
    identities.add(identity);
    surfaces.push(surface);
  }
  return { revision, surfaces };
}

function parseSurface(value: unknown): PluginSurfaceContribution | null {
  if (!isRecord(value)) return null;
  const ownerPluginId = readString(value.owner_plugin_id ?? value.ownerPluginId, 128);
  const id = readString(value.id, 64);
  const target = value.target;
  const kind = value.kind;
  const priority = value.priority ?? 0;
  const view = parseView(value.view);
  if (
    !ownerPluginId ||
    !identifierPattern.test(ownerPluginId) ||
    !id ||
    !identifierPattern.test(id) ||
    typeof target !== "string" ||
    !targets.has(target as PluginSurfaceTarget) ||
    typeof kind !== "string" ||
    !kinds.has(kind as PluginSurfaceKind) ||
    typeof priority !== "number" ||
    !Number.isInteger(priority) ||
    priority < -100 ||
    priority > 100 ||
    !view
  ) {
    return null;
  }
  return {
    ownerPluginId,
    id,
    target: target as PluginSurfaceTarget,
    kind: kind as PluginSurfaceKind,
    priority,
    source: "gateway",
    view,
  };
}

function parseView(value: unknown): PluginSurfaceView | null {
  if (!isRecord(value)) return null;
  const rawItems = value.items ?? [];
  const tone = value.tone ?? "neutral";
  const progress = value.progress ?? null;
  if (
    !Array.isArray(rawItems) ||
    rawItems.length > 20 ||
    typeof tone !== "string" ||
    !tones.has(tone as PluginSurfaceTone) ||
    (progress !== null &&
      (typeof progress !== "number" ||
        !Number.isFinite(progress) ||
        progress < 0 ||
        progress > 1))
  ) {
    return null;
  }
  const items: PluginSurfaceItem[] = [];
  for (const rawItem of rawItems) {
    if (!isRecord(rawItem)) return null;
    const text = readString(rawItem.text, 200);
    const detail = readString(rawItem.detail ?? "", 120, true);
    const completed = rawItem.completed ?? false;
    if (!text || detail === null || typeof completed !== "boolean") return null;
    items.push({ text, detail, completed });
  }
  const title = readString(value.title ?? "", 80, true);
  const text = readString(value.text ?? "", 400, true);
  const status = readString(value.status ?? "", 80, true);
  const detail = readString(value.detail ?? "", 120, true);
  const expiresAt = readString(value.expires_at ?? value.expiresAt ?? "", 64, true);
  if ([title, text, status, detail, expiresAt].some((item) => item === null)) {
    return null;
  }
  return {
    title: title ?? "",
    text: text ?? "",
    status: status ?? "",
    detail: detail ?? "",
    expiresAt: expiresAt ?? "",
    progress: progress as number | null,
    items,
    tone: tone as PluginSurfaceTone,
  };
}

function readString(
  value: unknown,
  maximum: number,
  allowEmpty = false,
): string | null {
  if (typeof value !== "string" || value.length > maximum) return null;
  const clean = value.trim();
  return clean || (allowEmpty ? "" : null);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
