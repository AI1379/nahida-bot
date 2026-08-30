import { describe, expect, it } from "vitest";

import {
  parseGatewayPluginSurfaceSnapshot,
  selectPluginSurfaces,
  type PluginSurfaceContribution,
} from "./pluginSurface";

describe("parseGatewayPluginSurfaceSnapshot", () => {
  it("normalizes a valid gateway snapshot", () => {
    expect(
      parseGatewayPluginSurfaceSnapshot({
        revision: 12,
        surfaces: [
          {
            owner_plugin_id: "example.schedule",
            id: "today",
            target: "desktop.home",
            kind: "list",
            priority: 20,
            view: {
              title: "今日安排",
              items: [
                { text: "晨会", detail: "09:30", completed: false },
              ],
              tone: "info",
            },
          },
        ],
      }),
    ).toEqual({
      revision: 12,
      surfaces: [
        {
          ownerPluginId: "example.schedule",
          id: "today",
          target: "desktop.home",
          kind: "list",
          priority: 20,
          source: "gateway",
          view: {
            title: "今日安排",
            text: "",
            status: "",
            detail: "",
            expiresAt: "",
            progress: null,
            items: [{ text: "晨会", detail: "09:30", completed: false }],
            tone: "info",
          },
        },
      ],
    });
  });

  it.each([
    { revision: -1, surfaces: [] },
    { revision: 1.5, surfaces: [] },
    {
      revision: 1,
      surfaces: [
        {
          owner_plugin_id: "example.schedule",
          id: "today",
          target: "unknown",
          kind: "list",
          view: {},
        },
      ],
    },
    {
      revision: 1,
      surfaces: [
        {
          owner_plugin_id: "example.schedule",
          id: "today",
          target: "desktop.home",
          kind: "progress",
          view: { progress: Number.NaN },
        },
      ],
    },
  ])("rejects malformed snapshots atomically", (snapshot) => {
    expect(parseGatewayPluginSurfaceSnapshot(snapshot)).toBeNull();
  });

  it("rejects duplicate plugin-owned surface identities", () => {
    const surface = {
      owner_plugin_id: "example.schedule",
      id: "today",
      target: "desktop.home",
      kind: "card",
      view: {},
    };

    expect(
      parseGatewayPluginSurfaceSnapshot({
        revision: 2,
        surfaces: [surface, surface],
      }),
    ).toBeNull();
  });
});

describe("selectPluginSurfaces", () => {
  const view = {
    title: "",
    text: "",
    status: "",
    detail: "",
    expiresAt: "",
    progress: null,
    items: [],
    tone: "neutral" as const,
  };

  it("filters one UI slot and applies stable priority ordering", () => {
    const surfaces: PluginSurfaceContribution[] = [
      {
        ownerPluginId: "example.low",
        id: "card",
        target: "desktop.sidebar",
        kind: "card",
        priority: 1,
        source: "gateway",
        view,
      },
      {
        ownerPluginId: "example.overlay",
        id: "badge",
        target: "pet.overlay",
        kind: "badge",
        priority: 100,
        source: "gateway",
        view,
      },
      {
        ownerPluginId: "example.high",
        id: "card",
        target: "desktop.sidebar",
        kind: "card",
        priority: 20,
        source: "gateway",
        view,
      },
    ];
    const originalOrder = [...surfaces];

    expect(
      selectPluginSurfaces(surfaces, "desktop.sidebar").map(
        (surface) => surface.ownerPluginId,
      ),
    ).toEqual(["example.high", "example.low"]);
    expect(surfaces).toEqual(originalOrder);
  });
});
