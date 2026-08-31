import { describe, expect, it } from "vitest";

import { parsePluginRuntimeSnapshot } from "./pluginRuntime";

describe("parsePluginRuntimeSnapshot", () => {
  it("parses bounded runtime facets and page contributions", () => {
    expect(
      parsePluginRuntimeSnapshot({
        generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        revision: 3,
        plugins: [
          {
            id: "nahida.pomodoro",
            name: "Pomodoro",
            version: "0.1.0",
            state: "enabled",
            configured_enabled: true,
            runtimes: {
              desktop: {
                entrypoint: "builtin:nahida.pomodoro",
                mode: "builtin",
              },
            },
            contributes: {
              pages: [
                {
                  id: "settings",
                  target: "desktop.main",
                  entry: "dist/settings.html",
                  title: "Settings",
                },
              ],
            },
          },
        ],
      }),
    ).toEqual({
      generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      revision: 3,
      plugins: [
        {
          id: "nahida.pomodoro",
          name: "Pomodoro",
          version: "0.1.0",
          state: "enabled",
          configuredEnabled: true,
          desktop: {
            entrypoint: "builtin:nahida.pomodoro",
            mode: "builtin",
          },
          pages: [
            {
              id: "settings",
              target: "desktop.main",
              entry: "dist/settings.html",
              title: "Settings",
            },
          ],
        },
      ],
    });
  });

  it("rejects duplicate plugins and invalid runtime modes atomically", () => {
    const plugin = {
      id: "example.plugin",
      name: "Example",
      version: "1.0.0",
      state: "enabled",
      configured_enabled: true,
      runtimes: {},
      contributes: {},
    };
    expect(
      parsePluginRuntimeSnapshot({
        generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        revision: 1,
        plugins: [plugin, plugin],
      }),
    ).toBeNull();
    expect(
      parsePluginRuntimeSnapshot({
        generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        revision: 1,
        plugins: [
          {
            ...plugin,
            runtimes: {
              desktop: { entrypoint: "x", mode: "native" },
            },
          },
        ],
      }),
    ).toBeNull();
    expect(
      parsePluginRuntimeSnapshot({
        generation: "not-a-generation",
        revision: 1,
        plugins: [],
      }),
    ).toBeNull();
  });
});
