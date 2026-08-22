import { describe, expect, it } from "vitest";

import {
  defaultRemoteControlPolicy,
  mergeActorIntoPolicy,
  parseRemoteControlPolicy,
  REMOTE_CONTROL_HARD_LIMITS,
  validateRemoteControlPolicy,
  type RemoteControlPolicy,
} from "@/services/remoteControlPolicy";

function withPolicy(patch: Partial<RemoteControlPolicy>): RemoteControlPolicy {
  return { ...defaultRemoteControlPolicy, ...patch };
}

describe("remoteControlPolicy", () => {
  it("defaults to disabled and round-trips the Rust-owned schema", () => {
    const parsed = parseRemoteControlPolicy(
      JSON.stringify(defaultRemoteControlPolicy),
    );
    expect(parsed.mode).toBe("disabled");
    expect(parsed.computerUse).toEqual({
      allowScreenCapture: false,
      allowInput: false,
    });
    expect(parsed.limits.timeoutMs).toBe(10_000);
  });

  it.each(["disabled", "scoped", "full_access"] as const)(
    "accepts %s mode",
    (mode) => {
      expect(parseRemoteControlPolicy(JSON.stringify({
        ...defaultRemoteControlPolicy,
        mode,
      })).mode).toBe(mode);
    },
  );

  it("migrates the legacy enabled policy to scoped", () => {
    const { mode: _mode, ...legacy } = defaultRemoteControlPolicy;
    expect(parseRemoteControlPolicy(JSON.stringify({
      ...legacy,
      enabled: true,
    })).mode).toBe("scoped");
  });

  it("rejects unknown fields and malformed profiles", () => {
    expect(() =>
      parseRemoteControlPolicy(
        JSON.stringify({ ...defaultRemoteControlPolicy, enabledByGateway: true }),
      ),
    ).toThrow(/unknown field/);

    expect(() =>
      parseRemoteControlPolicy(
        JSON.stringify({
          ...defaultRemoteControlPolicy,
          execProfiles: [{ id: "unsafe", program: "tool.exe" }],
        }),
      ),
    ).toThrow(/execution profile/);

    expect(() =>
      parseRemoteControlPolicy(
        JSON.stringify({
          ...defaultRemoteControlPolicy,
          computerUse: { allowScreenCapture: true },
        }),
      )
    ).toThrow(/computerUse/);
  });
});

describe("validateRemoteControlPolicy", () => {
  it("accepts the default policy", () => {
    expect(validateRemoteControlPolicy(defaultRemoteControlPolicy)).toBeNull();
  });

  it("rejects out-of-range limits and mismatched stdout/stderr sums", () => {
    expect(
      validateRemoteControlPolicy(
        withPolicy({ limits: { ...defaultRemoteControlPolicy.limits, timeoutMs: 0 } }),
      ),
    ).toMatch(/timeoutMs/);
    expect(
      validateRemoteControlPolicy(
        withPolicy({
          limits: {
            ...defaultRemoteControlPolicy.limits,
            timeoutMs: REMOTE_CONTROL_HARD_LIMITS.timeoutMs + 1,
          },
        }),
      ),
    ).toMatch(/timeoutMs/);
    expect(
      validateRemoteControlPolicy(
        withPolicy({
          limits: {
            ...defaultRemoteControlPolicy.limits,
            outputLimitBytes: 1000,
            stdoutLimitBytes: 800,
            stderrLimitBytes: 800,
          },
        }),
      ),
    ).toMatch(/stdout and stderr/);
  });

  it("rejects blank actors and non-absolute root paths", () => {
    expect(
      validateRemoteControlPolicy(
        withPolicy({ allowedActorAccountKeys: [" milky:user:1"] }),
      ),
    ).toMatch(/blank or padded/);
    expect(
      validateRemoteControlPolicy(
        withPolicy({ readRoots: [{ id: "notes", path: "relative/notes" }] }),
      ),
    ).toMatch(/absolute path/);
  });

  it("rejects profiles with unknown roots or forbidden interpreters", () => {
    expect(
      validateRemoteControlPolicy(
        withPolicy({
          execProfiles: [{
            id: "shell",
            program: "C:\\Windows\\System32\\cmd.exe",
            fixedArgs: [],
            cwdRootId: "",
            allowAdditionalArgs: false,
          }],
        }),
      ),
    ).toMatch(/forbidden interpreter/);

    const policy = withPolicy({
      readRoots: [{ id: "work", path: "C:\\work" }],
      execProfiles: [{
        id: "tool",
        program: "C:\\Tools\\tool.exe",
        fixedArgs: [],
        cwdRootId: "missing",
        allowAdditionalArgs: false,
      }],
    });
    expect(validateRemoteControlPolicy(policy)).toMatch(/unknown cwdRootId/);
  });

  it("accepts a complete scoped setup", () => {
    const policy = withPolicy({
      mode: "scoped",
      allowedActorAccountKeys: ["milky:user:2846390592"],
      readRoots: [{ id: "notes", path: "C:\\Users\\me\\notes" }],
      execProfiles: [{
        id: "tool",
        program: "C:\\Tools\\tool.exe",
        fixedArgs: ["--verbose"],
        cwdRootId: "notes",
        allowAdditionalArgs: true,
      }],
    });
    expect(validateRemoteControlPolicy(policy)).toBeNull();
  });
});

describe("mergeActorIntoPolicy", () => {
  it("appends a trimmed actor key immutably", () => {
    const result = mergeActorIntoPolicy(
      defaultRemoteControlPolicy,
      " milky:user:1 ",
    );
    expect(result.status).toBe("added");
    if (result.status !== "added") return;
    expect(result.policy.allowedActorAccountKeys).toEqual(["milky:user:1"]);
    expect(defaultRemoteControlPolicy.allowedActorAccountKeys).toEqual([]);
  });

  it("reports present for duplicates and rejects blanks or full lists", () => {
    expect(
      mergeActorIntoPolicy(
        withPolicy({ allowedActorAccountKeys: ["milky:user:1"] }),
        "milky:user:1",
      ).status,
    ).toBe("present");
    expect(
      mergeActorIntoPolicy(defaultRemoteControlPolicy, "  ").status,
    ).toBe("rejected");
    const full = withPolicy({
      allowedActorAccountKeys: Array.from(
        { length: REMOTE_CONTROL_HARD_LIMITS.maxActors },
        (_, index) => `milky:user:${index}`,
      ),
    });
    expect(mergeActorIntoPolicy(full, "milky:user:new").status).toBe("rejected");
  });
});
