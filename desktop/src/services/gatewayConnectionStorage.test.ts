import { describe, expect, it } from "vitest";

import {
  defaultGatewayConnectionSettings,
  sanitizeGatewayConnectionSettings,
} from "@/domain/gatewayConnection";

describe("gateway connection sanitization", () => {
  const defaultSettings = { ...defaultGatewayConnectionSettings };

  it("falls back to defaults on invalid input", () => {
    expect(sanitizeGatewayConnectionSettings(null)).toEqual(defaultSettings);
    expect(sanitizeGatewayConnectionSettings("nope")).toEqual(defaultSettings);
    expect(sanitizeGatewayConnectionSettings({ mode: "weird" }).mode).toBe("mock");
  });

  it("normalizes the WebSocket URL and strips unsupported schemes", () => {
    expect(
      sanitizeGatewayConnectionSettings({
        gatewayWsUrl: "ws://127.0.0.1:6185/api/nodes/ws/",
      }).gatewayWsUrl,
    ).toBe("ws://127.0.0.1:6185/api/nodes/ws");

    expect(
      sanitizeGatewayConnectionSettings({
        gatewayWsUrl: "file:///etc/passwd",
      }).gatewayWsUrl,
    ).toBe(defaultSettings.gatewayWsUrl);

    expect(
      sanitizeGatewayConnectionSettings({
        gatewayWsUrl: "javascript:alert(1)",
      }).gatewayWsUrl,
    ).toBe(defaultSettings.gatewayWsUrl);
  });

  it("rejects node ids that contain path-breaking characters", () => {
    expect(
      sanitizeGatewayConnectionSettings({ nodeId: "desktop/local" }).nodeId,
    ).toBe(defaultSettings.nodeId);
    expect(
      sanitizeGatewayConnectionSettings({ nodeId: "desktop local" }).nodeId,
    ).toBe(defaultSettings.nodeId);
    expect(sanitizeGatewayConnectionSettings({ nodeId: "desktop-01" }).nodeId).toBe(
      "desktop-01",
    );
  });

  it("keeps tokens truncated and trims whitespace", () => {
    expect(
      sanitizeGatewayConnectionSettings({ nodeToken: "  nt_abc.def  " }).nodeToken,
    ).toBe("nt_abc.def");
    const huge = "x".repeat(2048);
    expect(
      sanitizeGatewayConnectionSettings({ nodeToken: huge }).nodeToken.length,
    ).toBeLessThanOrEqual(512);
  });
});
