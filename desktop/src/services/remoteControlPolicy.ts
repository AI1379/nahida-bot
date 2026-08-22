import { invoke, isTauri } from "@tauri-apps/api/core";

export interface RemoteControlReadRoot {
  id: string;
  path: string;
}

export interface RemoteControlExecProfile {
  id: string;
  program: string;
  fixedArgs: string[];
  cwdRootId: string;
  allowAdditionalArgs: boolean;
}

export type RemoteControlMode = "disabled" | "scoped" | "full_access";

export interface RemoteControlPolicy {
  mode: RemoteControlMode;
  allowedActorAccountKeys: string[];
  readRoots: RemoteControlReadRoot[];
  execProfiles: RemoteControlExecProfile[];
  computerUse: {
    allowScreenCapture: boolean;
    allowInput: boolean;
  };
  limits: {
    timeoutMs: number;
    outputLimitBytes: number;
    stdoutLimitBytes: number;
    stderrLimitBytes: number;
    fileLimitBytes: number;
    maxAdditionalArgs: number;
    maxArgBytes: number;
  };
}

export const defaultRemoteControlPolicy: RemoteControlPolicy = {
  mode: "disabled",
  allowedActorAccountKeys: [],
  readRoots: [],
  execProfiles: [],
  computerUse: {
    allowScreenCapture: false,
    allowInput: false,
  },
  limits: {
    timeoutMs: 10_000,
    outputLimitBytes: 262_144,
    stdoutLimitBytes: 131_072,
    stderrLimitBytes: 131_072,
    fileLimitBytes: 262_144,
    maxAdditionalArgs: 16,
    maxArgBytes: 4_096,
  },
};

export function parseRemoteControlPolicy(source: string): RemoteControlPolicy {
  const value: unknown = JSON.parse(source);
  if (!isRecord(value)) throw new Error("Policy must be a JSON object.");
  const usesLegacyEnabled = "enabled" in value && !("mode" in value);
  requireExactKeys(value, [
    usesLegacyEnabled ? "enabled" : "mode",
    "allowedActorAccountKeys",
    "readRoots",
    "execProfiles",
    "computerUse",
    "limits",
  ], "policy");
  const mode = usesLegacyEnabled
    ? value.enabled === true ? "scoped" : "disabled"
    : value.mode;
  if (!usesLegacyEnabled && !isRemoteControlMode(mode)) {
    throw new Error("mode must be disabled, scoped, or full_access.");
  }
  if (usesLegacyEnabled && typeof value.enabled !== "boolean") {
    throw new Error("legacy enabled must be boolean.");
  }
  if (!isStringArray(value.allowedActorAccountKeys)) {
    throw new Error("allowedActorAccountKeys must be a string array.");
  }
  if (!Array.isArray(value.readRoots) || !value.readRoots.every(isReadRoot)) {
    throw new Error("Each read root needs string id/path fields.");
  }
  if (!Array.isArray(value.execProfiles) || !value.execProfiles.every(isExecProfile)) {
    throw new Error("Each execution profile is invalid.");
  }
  if (!isComputerUsePolicy(value.computerUse)) {
    throw new Error("computerUse is invalid.");
  }
  if (!isLimits(value.limits)) throw new Error("limits is invalid.");
  const { enabled: _legacyEnabled, ...policy } = value;
  return { ...policy, mode } as unknown as RemoteControlPolicy;
}

function isComputerUsePolicy(value: unknown): boolean {
  if (!isRecord(value)) return false;
  requireExactKeys(value, ["allowScreenCapture", "allowInput"], "computerUse");
  return typeof value.allowScreenCapture === "boolean" &&
    typeof value.allowInput === "boolean";
}

function isRemoteControlMode(value: unknown): value is RemoteControlMode {
  return value === "disabled" || value === "scoped" || value === "full_access";
}

function isReadRoot(value: unknown): boolean {
  if (!isRecord(value)) return false;
  requireExactKeys(value, ["id", "path"], "read root");
  return typeof value.id === "string" && typeof value.path === "string";
}

function isExecProfile(value: unknown): boolean {
  if (!isRecord(value)) return false;
  requireExactKeys(value, [
    "id",
    "program",
    "fixedArgs",
    "cwdRootId",
    "allowAdditionalArgs",
  ], "execution profile");
  return (
    typeof value.id === "string" &&
    typeof value.program === "string" &&
    isStringArray(value.fixedArgs) &&
    typeof value.cwdRootId === "string" &&
    typeof value.allowAdditionalArgs === "boolean"
  );
}

function isLimits(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const keys = [
    "timeoutMs",
    "outputLimitBytes",
    "stdoutLimitBytes",
    "stderrLimitBytes",
    "fileLimitBytes",
    "maxAdditionalArgs",
    "maxArgBytes",
  ];
  requireExactKeys(value, keys, "limits");
  return keys.every((key) => Number.isSafeInteger(value[key]) && Number(value[key]) >= 0);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function requireExactKeys(
  value: Record<string, unknown>,
  expected: string[],
  label: string,
): void {
  const actual = Object.keys(value);
  const unexpected = actual.find((key) => !expected.includes(key));
  const missing = expected.find((key) => !(key in value));
  if (unexpected) throw new Error(`${label} has unknown field ${unexpected}.`);
  if (missing) throw new Error(`${label} is missing ${missing}.`);
}

/**
 * Hard caps mirrored from `remote_control.rs`. Rust remains the enforcer;
 * these exist so the form can pre-validate and set input min/max attributes.
 */
export const REMOTE_CONTROL_HARD_LIMITS = {
  timeoutMs: 30_000,
  outputLimitBytes: 1_048_576,
  fileLimitBytes: 1_048_576,
  maxAdditionalArgs: 64,
  maxTotalArgs: 128,
  maxArgBytes: 8_192,
  maxActors: 32,
  maxRoots: 32,
  maxProfiles: 64,
} as const;

export function validateRemoteControlPolicy(
  policy: RemoteControlPolicy,
): string | null {
  const limits = policy.limits;
  const range = (
    name: string,
    value: number,
    minimum: number,
    maximum: number,
  ): string | null =>
    !Number.isSafeInteger(value) || value < minimum || value > maximum
      ? `${name} must be between ${minimum} and ${maximum}`
      : null;

  let error = range(
    "limits.timeoutMs",
    limits.timeoutMs,
    1,
    REMOTE_CONTROL_HARD_LIMITS.timeoutMs,
  );
  if (error) return error;
  error = range(
    "limits.outputLimitBytes",
    limits.outputLimitBytes,
    1,
    REMOTE_CONTROL_HARD_LIMITS.outputLimitBytes,
  );
  if (error) return error;
  error = range("limits.stdoutLimitBytes", limits.stdoutLimitBytes, 1, limits.outputLimitBytes);
  if (error) return error;
  error = range("limits.stderrLimitBytes", limits.stderrLimitBytes, 1, limits.outputLimitBytes);
  if (error) return error;
  if (limits.stdoutLimitBytes + limits.stderrLimitBytes > limits.outputLimitBytes) {
    return "stdout and stderr limits together exceed limits.outputLimitBytes";
  }
  error = range(
    "limits.fileLimitBytes",
    limits.fileLimitBytes,
    1,
    REMOTE_CONTROL_HARD_LIMITS.fileLimitBytes,
  );
  if (error) return error;
  if (
    !Number.isSafeInteger(limits.maxAdditionalArgs) ||
    limits.maxAdditionalArgs < 0 ||
    limits.maxAdditionalArgs > REMOTE_CONTROL_HARD_LIMITS.maxAdditionalArgs
  ) {
    return `limits.maxAdditionalArgs must be between 0 and ${REMOTE_CONTROL_HARD_LIMITS.maxAdditionalArgs}`;
  }
  error = range(
    "limits.maxArgBytes",
    limits.maxArgBytes,
    1,
    REMOTE_CONTROL_HARD_LIMITS.maxArgBytes,
  );
  if (error) return error;

  if (policy.allowedActorAccountKeys.length > REMOTE_CONTROL_HARD_LIMITS.maxActors) {
    return `allowedActorAccountKeys exceeds ${REMOTE_CONTROL_HARD_LIMITS.maxActors} entries`;
  }
  for (const actor of policy.allowedActorAccountKeys) {
    if (!actor.trim() || actor !== actor.trim()) {
      return "allowedActorAccountKeys may not contain blank or padded values";
    }
  }
  if (policy.readRoots.length > REMOTE_CONTROL_HARD_LIMITS.maxRoots) {
    return `readRoots exceeds ${REMOTE_CONTROL_HARD_LIMITS.maxRoots} entries`;
  }
  if (policy.execProfiles.length > REMOTE_CONTROL_HARD_LIMITS.maxProfiles) {
    return `execProfiles exceeds ${REMOTE_CONTROL_HARD_LIMITS.maxProfiles} entries`;
  }

  const seenRootIds = new Set<string>();
  for (const root of policy.readRoots) {
    if (!root.id.trim() || root.id !== root.id.trim() || seenRootIds.has(root.id)) {
      return "read root ids must be non-empty, trimmed, and unique";
    }
    seenRootIds.add(root.id);
    if (!root.path.trim() || !isAbsoluteLocalPath(root.path)) {
      return `read root ${root.id} must have an absolute path`;
    }
  }

  const seenProfileIds = new Set<string>();
  for (const profile of policy.execProfiles) {
    if (
      !profile.id.trim() ||
      profile.id !== profile.id.trim() ||
      seenProfileIds.has(profile.id)
    ) {
      return "execution profile ids must be non-empty, trimmed, and unique";
    }
    seenProfileIds.add(profile.id);
    if (!profile.program.trim() || !isAbsoluteLocalPath(profile.program)) {
      return `execution profile ${profile.id} program must be an absolute path`;
    }
    if (isForbiddenInterpreter(profile.program)) {
      return `execution profile ${profile.id} uses a forbidden interpreter`;
    }
    if (profile.fixedArgs.length > REMOTE_CONTROL_HARD_LIMITS.maxTotalArgs) {
      return `execution profile ${profile.id} has too many fixed arguments`;
    }
    for (const argument of profile.fixedArgs) {
      if (
        argument.includes("\0") ||
        argument.length > limits.maxArgBytes
      ) {
        return `execution profile ${profile.id} has an invalid fixed argument`;
      }
    }
    if (!seenRootIds.has(profile.cwdRootId)) {
      return `execution profile ${profile.id} references an unknown cwdRootId`;
    }
  }
  return null;
}

const FORBIDDEN_INTERPRETER_EXTENSIONS = new Set([
  "bat",
  "cmd",
  "ps1",
  "vbs",
  "vbe",
  "js",
  "jse",
  "wsf",
  "wsh",
]);

const FORBIDDEN_INTERPRETER_NAMES = new Set([
  "cmd",
  "powershell",
  "pwsh",
  "wscript",
  "cscript",
  "sh",
  "bash",
  "zsh",
  "fish",
  "python",
  "python3",
  "pythonw",
  "node",
  "deno",
  "bun",
  "ruby",
  "perl",
  "php",
  "lua",
]);

function isForbiddenInterpreter(program: string): boolean {
  const fileName = program.trim().split(/[\\/]/).pop() ?? "";
  const dot = fileName.lastIndexOf(".");
  if (dot > 0) {
    const extension = fileName.slice(dot + 1).toLowerCase();
    if (FORBIDDEN_INTERPRETER_EXTENSIONS.has(extension)) return true;
  }
  const withoutExe = fileName.toLowerCase().replace(/\.exe$/, "");
  return FORBIDDEN_INTERPRETER_NAMES.has(withoutExe);
}

function isAbsoluteLocalPath(value: string): boolean {
  return /^([A-Za-z]:[\\/]|\\\\|\/)/.test(value);
}

export type ActorMergeResult =
  | { status: "added"; policy: RemoteControlPolicy }
  | { status: "present" }
  | { status: "rejected"; reason: string };

export function mergeActorIntoPolicy(
  policy: RemoteControlPolicy,
  actorAccountKey: string,
): ActorMergeResult {
  const trimmed = actorAccountKey.trim();
  if (!trimmed) return { status: "rejected", reason: "actor key is empty" };
  if (policy.allowedActorAccountKeys.includes(trimmed)) {
    return { status: "present" };
  }
  if (policy.allowedActorAccountKeys.length >= REMOTE_CONTROL_HARD_LIMITS.maxActors) {
    return {
      status: "rejected",
      reason: `whitelist already holds ${REMOTE_CONTROL_HARD_LIMITS.maxActors} actors`,
    };
  }
  return {
    status: "added",
    policy: {
      ...policy,
      allowedActorAccountKeys: [...policy.allowedActorAccountKeys, trimmed],
    },
  };
}

export interface ActorWhitelistUpdate {
  added: boolean;
  error?: string;
}

/**
 * Append a paired actor key to the local remote-control whitelist.
 * Never throws: pairing success must not depend on this side effect.
 */
export async function addPairedActorToRemoteControlPolicy(
  actorAccountKey: string,
): Promise<ActorWhitelistUpdate> {
  if (!isTauri()) return { added: false, error: "not in the desktop app" };
  try {
    const current = await invoke<RemoteControlPolicy>(
      "remote_control_policy_read",
    );
    const merged = mergeActorIntoPolicy(current, actorAccountKey);
    if (merged.status === "present") return { added: false };
    if (merged.status === "rejected") {
      return { added: false, error: merged.reason };
    }
    await invoke("remote_control_policy_save", { policy: merged.policy });
    return { added: true };
  } catch (error) {
    return {
      added: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}
