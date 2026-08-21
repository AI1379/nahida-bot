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
