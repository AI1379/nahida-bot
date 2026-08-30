import { describe, expect, it } from "vitest";

import { createPluginSurfacePreview } from "./pluginSurfacePreview";

describe("createPluginSurfacePreview", () => {
  it("covers every host-rendered UI slot with local fixtures", () => {
    const surfaces = createPluginSurfacePreview();

    expect(surfaces.map((surface) => surface.target).sort()).toEqual([
      "desktop.home",
      "desktop.sidebar",
      "pet.drawer",
      "pet.overlay",
    ]);
    expect(surfaces.every((surface) => surface.source === "local")).toBe(true);
  });
});
