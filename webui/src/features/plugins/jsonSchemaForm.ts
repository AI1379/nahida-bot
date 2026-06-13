/**
 * jsonSchemaForm.ts
 *
 * Pure TypeScript utilities for converting config schema data → flat field descriptors,
 * plus config diff/patch helpers used by PluginConfigForm.vue.
 *
 * Two sources for field generation:
 * 1. JSON Schema (`schemaToFields`) — when a plugin declares `config_schema` in manifest
 * 2. Schema entries from `/api/config/schema` (`schemaEntriesToFields`) — works for ALL
 *    plugins because the backend can infer types from actual config values
 */
import type { ConfigPatchChange } from "@/api/schemas";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FieldKind =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "select"
  | "array-string"
  | "array-number"
  | "secret";

export interface SchemaField {
  /** Dot-notation path relative to plugin root, e.g. "database.host" */
  path: string;
  /** Human-readable label (from schema "title" or derived from key) */
  label: string;
  /** From schema "description" */
  description?: string;
  kind: FieldKind;
  required: boolean;
  default?: unknown;
  /** For "select" kind — the enum values */
  options?: string[];
  /** Constraint metadata */
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
}

/** Shape returned by /api/config/schema entries */
export interface SchemaEntryData {
  path: string;
  type: string;
  default: string;
  constraints: string;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

type ConfigMap = Record<string, unknown>;

function isRecord(value: unknown): value is ConfigMap {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

const SECRET_PATTERNS = /token|api_?key|secret|password|auth_?key|access_?key/i;

export function isSecretKey(key: string): boolean {
  return SECRET_PATTERNS.test(key);
}

/** Capitalise first letter, replace underscores / hyphens with spaces. */
function humanise(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Extract the last segment of a dot-path for display. */
function lastSegment(path: string): string {
  const idx = path.lastIndexOf(".");
  return idx >= 0 ? path.slice(idx + 1) : path;
}

// ---------------------------------------------------------------------------
// schemaEntriesToFields — /api/config/schema entries → SchemaField[]
// ---------------------------------------------------------------------------

/**
 * Convert flat schema entries from `/api/config/schema` into `SchemaField[]`.
 * This works for ALL plugins — the backend already infers types for plugins
 * without `config_schema`.
 *
 * @param entries  All schema entries (including non-plugin entries)
 * @param pluginId  Only include entries whose path starts with `pluginId.`
 * @param currentValues  Current config values (for determining array item types)
 */
export function schemaEntriesToFields(
  entries: SchemaEntryData[],
  pluginId: string,
): SchemaField[] {
  const prefix = pluginId + ".";
  const fields: SchemaField[] = [];
  const containerPaths = new Set(
    entries
      .filter((entry) =>
        entries.some((candidate) => candidate.path.startsWith(entry.path + ".")),
      )
      .map((entry) => entry.path),
  );

  for (const entry of entries) {
    if (!entry.path.startsWith(prefix)) continue;
    // Skip the plugin-level top entry (type is like "PluginConfig (...)")
    if (entry.path === pluginId) continue;
    // Skip container entries — their children are rendered as separate fields.
    if (entry.type === "dict" || containerPaths.has(entry.path)) continue;

    const relativePath = entry.path.slice(prefix.length);
    const key = lastSegment(relativePath);
    const kind = inferKindFromType(key, entry.type);
    const options = kind === "select" ? literalOptions(entry.type) : undefined;

    fields.push({
      path: relativePath,
      label: humanise(key),
      kind,
      required: false,
      default: parseDefault(entry.default),
      options,
    });
  }

  return fields;
}

/**
 * Infer FieldKind from the backend's human-readable type string.
 * Backend types: "str", "bool", "int", "float", "list[str]", "list[int]", etc.
 */
function inferKindFromType(
  key: string,
  type: string,
): FieldKind {
  if (isSecretKey(key)) return "secret";
  if (literalOptions(type)) return "select";
  if (type === "bool") return "boolean";
  if (type === "int") return "integer";
  if (type === "float") return "number";
  if (type.startsWith("list[")) {
    const inner = type.slice(5, -1);
    if (inner === "int" || inner === "float" || inner === "number") return "array-number";
    return "array-string";
  }
  // "str" or anything else → string
  return "string";
}

function literalOptions(type: string): string[] | undefined {
  const literalBody = type.match(/^Literal\[(.*)\]$/)?.[1] ?? type;
  const separator = type.startsWith("Literal[") ? "," : "|";
  const options = literalBody
    .split(separator)
    .map((value) => value.trim())
    .map((value) => {
      const quoted = value.match(/^(['"])(.*)\1$/);
      return quoted?.[2];
    });

  return options.length > 1
    && options.every((value): value is string => value !== undefined)
    ? options
    : undefined;
}

function parseDefault(raw: string): unknown {
  if (raw === "" || raw === "required" || raw === "-") return undefined;
  if (raw === '""') return "";
  if (raw === "True") return true;
  if (raw === "False") return false;
  if (raw === "None") return null;
  if (/^-?\d+$/.test(raw)) return Number.parseInt(raw, 10);
  if (/^-?(?:\d+\.\d*|\d*\.\d+)$/.test(raw)) return Number.parseFloat(raw);
  if (
    (raw.startsWith("[") && raw.endsWith("]"))
    || (raw.startsWith("{") && raw.endsWith("}"))
  ) {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}

// ---------------------------------------------------------------------------
// schemaToFields — JSON Schema → flat SchemaField[]  (kept for future use)
// ---------------------------------------------------------------------------

/**
 * Walk a JSON Schema's `properties` and produce a flat list of `SchemaField`s.
 * Nested objects are flattened to dot-notation paths.
 *
 * @param schema  The full JSON Schema object (must contain `properties`).
 * @param defaults  Default config values (from manifest.config).
 */
export function schemaToFields(
  schema: ConfigMap,
  defaults: ConfigMap,
): SchemaField[] {
  const props = schema.properties as ConfigMap | undefined;
  if (!props) return [];

  const requiredKeys = new Set<string>(
    Array.isArray(schema.required) ? (schema.required as string[]) : [],
  );

  const fields: SchemaField[] = [];

  function walk(
    properties: ConfigMap,
    parentPath: string,
    parentDefaults: ConfigMap,
  ) {
    for (const [key, rawDef] of Object.entries(properties)) {
      if (!isRecord(rawDef)) continue;

      const path = parentPath ? `${parentPath}.${key}` : key;
      const def = rawDef as ConfigMap;

      // Determine field kind
      const hasEnum = Array.isArray(def.enum) && def.enum.length > 0;
      const schemaType = def.type as string | undefined;

      // Resolve default from schema "default" or from the defaults map
      const fieldDefault =
        def.default ?? parentDefaults[key];

      if (hasEnum) {
        fields.push({
          path,
          label: (def.title as string) ?? humanise(key),
          description: def.description as string | undefined,
          kind: "select",
          required: requiredKeys.has(key),
          default: fieldDefault,
          options: (def.enum as string[]).map(String),
        });
      } else if (schemaType === "object" && isRecord(def.properties)) {
        // Recurse into nested object
        const nestedDefaults =
          isRecord(fieldDefault) ? fieldDefault : {};
        walk(def.properties as ConfigMap, path, nestedDefaults);
      } else {
        const kind = inferKindFromSchema(key, def);
        fields.push({
          path,
          label: (def.title as string) ?? humanise(key),
          description: def.description as string | undefined,
          kind,
          required: requiredKeys.has(key),
          default: fieldDefault,
          options: kind === "select" ? (def.enum as string[]).map(String) : undefined,
          minimum: def.minimum as number | undefined,
          maximum: def.maximum as number | undefined,
          minLength: def.minLength as number | undefined,
          maxLength: def.maxLength as number | undefined,
          minItems: def.minItems as number | undefined,
          maxItems: def.maxItems as number | undefined,
        });
      }
    }
  }

  walk(props, "", defaults);
  return fields;
}

function inferKindFromSchema(key: string, def: ConfigMap): FieldKind {
  if (isSecretKey(key)) return "secret";

  const t = def.type as string | undefined;

  if (t === "boolean") return "boolean";
  if (t === "integer") return "integer";
  if (t === "number") return "number";

  if (t === "array") {
    const items = def.items as ConfigMap | undefined;
    if (items?.type === "integer" || items?.type === "number") {
      return "array-number";
    }
    return "array-string";
  }

  // Fallback: string
  return "string";
}

// ---------------------------------------------------------------------------
// Value access helpers
// ---------------------------------------------------------------------------

export function getAtPath(root: ConfigMap, path: string): unknown {
  const parts = path.split(".");
  let current: unknown = root;
  for (const part of parts) {
    if (!isRecord(current)) return undefined;
    current = current[part];
  }
  return current;
}

export function setAtPath(root: ConfigMap, path: string, value: unknown): void {
  const parts = path.split(".");
  let current: ConfigMap = root;
  for (const part of parts.slice(0, -1)) {
    if (!isRecord(current[part])) current[part] = {};
    current = current[part] as ConfigMap;
  }
  current[parts[parts.length - 1]] = value;
}

export function removeAtPath(root: ConfigMap, path: string): void {
  const parts = path.split(".");
  let current: unknown = root;
  for (const part of parts.slice(0, -1)) {
    if (!isRecord(current)) return;
    current = current[part];
  }
  if (isRecord(current)) delete current[parts[parts.length - 1]];
}

// ---------------------------------------------------------------------------
// ConfigMap helpers
// ---------------------------------------------------------------------------

export function cloneConfig(value: ConfigMap): ConfigMap {
  return JSON.parse(JSON.stringify(value));
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ---------------------------------------------------------------------------
// buildPluginChanges — diff two config states → ConfigPatchChange[]
// ---------------------------------------------------------------------------

/**
 * Diff `before` and `after` config maps and produce `ConfigPatchChange[]`.
 * All paths are prefixed with `{pluginId}.` so they map to the top-level
 * plugin key in config.yaml.
 */
export function buildPluginChanges(
  pluginId: string,
  before: ConfigMap,
  after: ConfigMap,
  redactedPaths: Set<string> = new Set(),
): ConfigPatchChange[] {
  const changes: ConfigPatchChange[] = [];
  diffConfig(pluginId, before, after, changes, redactedPaths);
  return changes;
}

function diffConfig(
  prefix: string,
  before: unknown,
  after: unknown,
  out: ConfigPatchChange[],
  redactedPaths: Set<string>,
): void {
  if (before === undefined && after === undefined) return;

  if (before === undefined) {
    out.push({ path: prefix, value: after });
    return;
  }

  if (after === undefined) {
    out.push({ path: prefix, remove: true });
    return;
  }

  if (isRecord(before) && isRecord(after)) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    for (const key of keys) {
      diffConfig(
        prefix ? `${prefix}.${key}` : key,
        before[key],
        after[key],
        out,
        redactedPaths,
      );
    }
    return;
  }

  if (sameValue(before, after)) return;

  // If both are "***" on a redacted path, skip (user didn't change it)
  if (redactedPaths.has(prefix) && before === "***" && after === "***") return;

  out.push({
    path: prefix,
    value: after,
    secret_action: redactedPaths.has(prefix) ? "replace" : undefined,
  });
}
