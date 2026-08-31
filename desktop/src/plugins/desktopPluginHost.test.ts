import { computed, type Component } from "vue";
import { describe, expect, it, vi } from "vitest";

import type { PluginSurfaceView } from "@/domain/pluginSurface";
import type { PluginRuntimeSnapshot } from "@/domain/pluginRuntime";
import {
  DesktopPluginHost,
  type DesktopPluginDefinition,
  type DesktopPluginFacetManifest,
  type DesktopPluginHostAdapter,
} from "./desktopPluginHost";

const emptyView: PluginSurfaceView = {
  title: "Timer",
  text: "",
  status: "",
  detail: "",
  expiresAt: "",
  progress: null,
  items: [],
  tone: "neutral",
};

function manifest(
  id: string,
  capability = `${id}.status`,
): DesktopPluginFacetManifest {
  return {
    id,
    name: id,
    version: "1.0.0",
    entrypoint: `builtin:${id}`,
    builtin: true,
    contributes: {
      capabilities: [capability],
      actions: ["start"],
      surfaces: [
        {
          id: "timer",
          target: "pet.overlay",
          kind: "countdown",
          priority: 10,
        },
      ],
      settingsPanels: [
        {
          id: "settings",
          section: { id: "focus", label: "Focus", hint: "Timer", order: 20 },
          placements: ["settings"],
          component: {} as Component,
        },
      ],
    },
  };
}

function definition(
  id: string,
  capability = `${id}.status`,
): DesktopPluginDefinition {
  return {
    manifest: manifest(id, capability),
    activate(context) {
      context.setSurface("timer", emptyView);
      return {
        capabilities: {
          [capability]: () => ({ ok: true, result: { capability } }),
        },
        actions: {
          start: () => ({ ok: true, result: { started: true } }),
        },
        dispose: vi.fn(),
      };
    },
  };
}

function createHost() {
  const adapter: DesktopPluginHostAdapter = {
    emitEvent: vi.fn(),
    upsertSurface: vi.fn(),
    removeSurface: vi.fn(),
  };
  return { adapter, host: new DesktopPluginHost(adapter) };
}

function runtimeSnapshot(
  revision: number,
  state: "enabled" | "disabled",
  overrides: Partial<PluginRuntimeSnapshot["plugins"][number]> = {},
): PluginRuntimeSnapshot {
  return {
    generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    revision,
    plugins: [
      {
        id: "example.focus",
        name: "example.focus",
        version: "1.0.0",
        state,
        configuredEnabled: state === "enabled",
        desktop: {
          entrypoint: "builtin:example.focus",
          mode: "builtin",
        },
        pages: [],
        ...overrides,
      },
    ],
  };
}

describe("DesktopPluginHost", () => {
  it("owns declared capabilities, actions, surfaces, and settings panels", () => {
    const { adapter, host } = createHost();
    const panelCount = computed(
      () => host.settingsPanels("settings").length,
    );

    expect(host.activate(definition("example.focus")).status).toBe("active");
    expect(panelCount.value).toBe(1);
    expect(adapter.upsertSurface).toHaveBeenCalledWith(
      expect.objectContaining({
        ownerPluginId: "example.focus",
        id: "timer",
        source: "local",
      }),
    );
    expect(host.executeCapability("example.focus.status", {})).toEqual({
      ok: true,
      result: { capability: "example.focus.status" },
    });
    expect(host.invokeAction("example.focus", "start")).toEqual({
      ok: true,
      result: { started: true },
    });
    expect(host.settingsSections()).toEqual([
      { id: "focus", label: "Focus", hint: "Timer", order: 20 },
    ]);
    expect(host.settingsPanels("settings", "focus")[0]?.ownerPluginId).toBe(
      "example.focus",
    );

    expect(host.deactivate("example.focus")).toBe(true);
    expect(adapter.removeSurface).toHaveBeenCalledWith(
      "example.focus",
      "timer",
    );
    expect(host.executeCapability("example.focus.status", {})).toBeNull();
    expect(panelCount.value).toBe(0);
    expect(host.activate(definition("example.focus")).status).toBe("active");
    expect(panelCount.value).toBe(1);
  });

  it("isolates duplicate capability owners without replacing the first plugin", () => {
    const { host } = createHost();
    const capability = "desktop.timer.status";

    expect(host.activate(definition("example.first", capability)).status).toBe(
      "active",
    );
    const second = host.activate(definition("example.second", capability));

    expect(second).toMatchObject({ status: "error" });
    expect(host.executeCapability(capability, {})).toEqual({
      ok: true,
      result: { capability },
    });
  });

  it("rejects undeclared runtime handlers and clears activation side effects", () => {
    const { adapter, host } = createHost();
    const invalid = definition("example.invalid");
    invalid.activate = (context) => {
      context.setSurface("timer", emptyView);
      return {
        capabilities: {
          "example.invalid.status": () => ({ ok: true, result: {} }),
          "example.invalid.extra": () => ({ ok: true, result: {} }),
        },
        actions: {
          start: () => ({ ok: true, result: {} }),
        },
        dispose: vi.fn(),
      };
    };

    expect(host.activate(invalid)).toMatchObject({ status: "error" });
    expect(adapter.removeSurface).toHaveBeenCalledWith(
      "example.invalid",
      "timer",
    );
  });

  it("reconciles Gateway enablement and ignores stale snapshots", () => {
    const { host } = createHost();
    host.activateAll([definition("example.focus")]);

    expect(host.reconcile(runtimeSnapshot(1, "disabled"))).toMatchObject({
      applied: true,
      deactivated: ["example.focus"],
    });
    expect(host.settingsPanels("settings")).toHaveLength(0);

    expect(host.reconcile(runtimeSnapshot(2, "enabled"))).toMatchObject({
      applied: true,
      activated: ["example.focus"],
    });
    expect(host.settingsPanels("settings")).toHaveLength(1);
    expect(host.reconcile(runtimeSnapshot(1, "disabled")).applied).toBe(false);
    expect(host.settingsPanels("settings")).toHaveLength(1);
  });

  it("fails closed on incompatible or missing Desktop artifacts", () => {
    const { host } = createHost();
    host.activateAll([definition("example.focus")]);

    host.reconcile(
      runtimeSnapshot(1, "enabled", {
        version: "2.0.0",
      }),
    );
    expect(host.getRuntime("example.focus")).toBeNull();
    expect(host.listSyncIssues()).toEqual([
      expect.objectContaining({ code: "version_mismatch" }),
    ]);

    host.reconcile({
      generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      revision: 2,
      plugins: [
        {
          ...runtimeSnapshot(2, "enabled").plugins[0]!,
          id: "example.external",
          desktop: { entrypoint: "dist/plugin.js", mode: "javascript" },
        },
      ],
    });
    expect(host.listSyncIssues()).toEqual([
      expect.objectContaining({
        pluginId: "example.external",
        code: "unsupported_mode",
      }),
    ]);
  });

  it("accepts a lower revision after the Gateway generation changes", () => {
    const { host } = createHost();
    host.activateAll([definition("example.focus")]);
    host.reconcile(runtimeSnapshot(8, "disabled"));

    expect(
      host.reconcile({
        ...runtimeSnapshot(1, "enabled"),
        generation: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      }),
    ).toMatchObject({ applied: true, activated: ["example.focus"] });
  });
});
