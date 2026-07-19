import { describe, expect, it } from "vitest";

import {
  defaultGatewayConnection,
  sanitizeGatewayConnection,
} from "./gatewayConnectionStorage";

describe("gateway connection sanitization", () => {
  it("falls back to defaults on invalid input", () => {
    expect(sanitizeGatewayConnection(null)).toEqual(defaultGatewayConnection());
    expect(sanitizeGatewayConnection("nope")).toEqual(defaultGatewayConnection());
    expect(sanitizeGatewayConnection({ mode: "weird" }).mode).toBe("mock");
  });

  it("normalizes the WebSocket URL and strips unsupported schemes", () => {
    expect(
      sanitizeGatewayConnection({
        gatewayWsUrl: "ws://127.0.0.1:6185/api/nodes/ws/",
      }).gatewayWsUrl,
    ).toBe("ws://127.0.0.1:6185/api/nodes/ws");

    expect(
      sanitizeGatewayConnection({
        gatewayWsUrl: "file:///etc/passwd",
      }).gatewayWsUrl,
    ).toBe(defaultGatewayConnection().gatewayWsUrl);

    expect(
      sanitizeGatewayConnection({
        gatewayWsUrl: "javascript:alert(1)",
      }).gatewayWsUrl,
    ).toBe(defaultGatewayConnection().gatewayWsUrl);
  });

  it("rejects node ids that contain path-breaking characters", () => {
    expect(
      sanitizeGatewayConnection({ nodeId: "desktop/local" }).nodeId,
    ).toBe(defaultGatewayConnection().nodeId);
    expect(
      sanitizeGatewayConnection({ nodeId: "desktop local" }).nodeId,
    ).toBe(defaultGatewayConnection().nodeId);
    expect(sanitizeGatewayConnection({ nodeId: "desktop-01" }).nodeId).toBe(
      "desktop-01",
    );
  });

  it("keeps tokens truncated and trims whitespace", () => {
    expect(
      sanitizeGatewayConnection({ nodeToken: "  nt_abc.def  " }).nodeToken,
    ).toBe("nt_abc.def");
    const huge = "x".repeat(2048);
    expect(
      sanitizeGatewayConnection({ nodeToken: huge }).nodeToken.length,
    ).toBeLessThanOrEqual(512);
  });
});
