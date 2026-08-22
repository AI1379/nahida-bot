import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  invoke: mocks.invoke,
  isTauri: () => true,
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: mocks.listen,
}));

import { defaultGatewayConnectionSettings } from "@/domain/gatewayConnection";
import { TauriGatewayNodeEventSource } from "./desktopEventSource";

describe("TauriGatewayNodeEventSource", () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.listen.mockReset();
  });

  it("disconnects the native retry loop after an authentication failure", async () => {
    let listener: ((event: { payload: unknown }) => void) | undefined;
    mocks.listen.mockImplementation(async (_name, handler) => {
      listener = handler;
      return vi.fn();
    });
    mocks.invoke.mockResolvedValue({
      connected: false,
      registered: false,
      nodeId: "desktop-local",
      gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
    });
    const events: unknown[] = [];
    const source = new TauriGatewayNodeEventSource();

    source.start((event) => events.push(event), {
      connection: {
        ...defaultGatewayConnectionSettings,
        mode: "gateway",
        nodeToken: "nt_abc.def",
      },
    });
    await vi.waitFor(() => {
      expect(mocks.invoke).toHaveBeenCalledWith(
        "gateway_node_connect",
        expect.any(Object),
      );
    });

    listener?.({
      payload: {
        type: "status_changed",
        at: "2026-08-08T00:00:00Z",
        status: {
          connected: false,
          registered: false,
          nodeId: "desktop-local",
          gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
          lastError: "HTTP error: 401 Unauthorized",
        },
      },
    });

    expect(events).toContainEqual(
      expect.objectContaining({
        type: "connection.changed",
        authRequired: true,
      }),
    );
    expect(mocks.invoke).toHaveBeenCalledWith("gateway_node_disconnect");
  });

  it("does not connect when explicitly stopped while listener setup is pending", async () => {
    let resolveListen: ((unlisten: () => void) => void) | undefined;
    const unlisten = vi.fn();
    mocks.listen.mockReturnValue(
      new Promise<() => void>((resolve) => {
        resolveListen = resolve;
      }),
    );
    mocks.invoke.mockResolvedValue(undefined);
    const source = new TauriGatewayNodeEventSource();

    source.start(vi.fn(), {
      connection: {
        ...defaultGatewayConnectionSettings,
        mode: "gateway",
        nodeToken: "nt_abc.def",
      },
    });
    source.stop();
    resolveListen?.(unlisten);
    await Promise.resolve();
    await Promise.resolve();

    expect(unlisten).toHaveBeenCalledOnce();
    expect(mocks.invoke).not.toHaveBeenCalledWith(
      "gateway_node_connect",
      expect.anything(),
    );
  });

  it("returns the Gateway protocol rejection instead of treating invoke resolution as success", async () => {
    mocks.listen.mockResolvedValue(vi.fn());
    mocks.invoke.mockImplementation(async (command: string) => {
      if (command === "gateway_node_connect") {
        return {
          connected: true,
          registered: true,
          nodeId: "desktop-local",
          gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
          defaultSessionId: "session-1",
        };
      }
      if (command === "gateway_node_submit_input") {
        return {
          id: "request-1",
          ok: false,
          error: {
            message: "actor binding is required",
            retryable: false,
          },
        };
      }
      return undefined;
    });
    const source = new TauriGatewayNodeEventSource();
    source.start(vi.fn(), {
      connection: {
        ...defaultGatewayConnectionSettings,
        mode: "gateway",
        nodeToken: "nt_abc.def",
      },
    });
    await vi.waitFor(() => {
      expect(mocks.invoke).toHaveBeenCalledWith(
        "gateway_node_connect",
        expect.any(Object),
      );
    });

    await expect(source.submitUserMessage("hello", "session-1")).resolves.toEqual({
      ok: false,
      error: "actor binding is required",
      retryable: false,
    });
  });

  it("reports capability success only after the renderer handler returns", async () => {
    let listener: ((event: { payload: unknown }) => void) | undefined;
    mocks.listen.mockImplementation(async (_name, handler) => {
      listener = handler;
      return vi.fn();
    });
    mocks.invoke.mockResolvedValue({
      connected: true,
      registered: true,
      nodeId: "desktop-local",
      gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
    });
    const source = new TauriGatewayNodeEventSource();
    source.start((event) => {
      if (event.type !== "capability.invoked") return;
      return { ok: true, result: { applied: true } };
    });
    await vi.waitFor(() => expect(listener).toBeDefined());

    listener?.({
      payload: {
        type: "capability_invoke",
        at: "2026-08-08T00:00:00Z",
        invokeId: "inv_success",
        capability: "desktop.notification.show",
        arguments: { message: "hello" },
      },
    });

    expect(mocks.invoke).toHaveBeenCalledWith(
      "gateway_node_complete_capability",
      {
        result: {
          invokeId: "inv_success",
          ok: true,
          result: { applied: true },
        },
      },
    );
  });

  it("reports renderer exceptions as structured capability failures", async () => {
    let listener: ((event: { payload: unknown }) => void) | undefined;
    mocks.listen.mockImplementation(async (_name, handler) => {
      listener = handler;
      return vi.fn();
    });
    mocks.invoke.mockResolvedValue({
      connected: true,
      registered: true,
      nodeId: "desktop-local",
      gatewayUrl: "ws://127.0.0.1:6185/api/nodes/ws",
    });
    const source = new TauriGatewayNodeEventSource();
    source.start((event) => {
      if (event.type === "capability.invoked") throw new Error("render failed");
    });
    await vi.waitFor(() => expect(listener).toBeDefined());

    listener?.({
      payload: {
        type: "capability_invoke",
        at: "2026-08-08T00:00:00Z",
        invokeId: "inv_failure",
        capability: "desktop.notification.show",
        arguments: { message: "hello" },
      },
    });

    expect(mocks.invoke).toHaveBeenCalledWith(
      "gateway_node_complete_capability",
      {
        result: {
          invokeId: "inv_failure",
          ok: false,
          error: {
            code: "capability_failed",
            message: "renderer capability execution failed: Error: render failed",
            retryable: false,
          },
        },
      },
    );
  });
});
