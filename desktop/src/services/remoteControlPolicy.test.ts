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
    expect(parsed.enabled).toBe(false);
    expect(parsed.limits.timeoutMs).toBe(10_000);
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
