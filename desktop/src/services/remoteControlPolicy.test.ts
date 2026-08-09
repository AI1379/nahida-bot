import { describe, expect, it } from "vitest";

import {
  defaultRemoteControlPolicy,
  parseRemoteControlPolicy,
} from "@/services/remoteControlPolicy";

describe("remoteControlPolicy", () => {
  it("defaults to disabled and round-trips the Rust-owned schema", () => {
    const parsed = parseRemoteControlPolicy(
      JSON.stringify(defaultRemoteControlPolicy),
    );
    expect(parsed.mode).toBe("disabled");
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
  });
});
