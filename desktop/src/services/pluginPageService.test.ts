import { beforeEach, describe, expect, it, vi } from "vitest";

import { defaultGatewayConnectionSettings } from "@/domain/gatewayConnection";
import type { ActiveRemotePluginPage } from "@/plugins/desktopPluginContract";
import {
  fetchDesktopPluginPage,
  sandboxPluginPageDocument,
} from "./pluginPageService";

const activePage: ActiveRemotePluginPage = {
  pluginId: "demo.plugin",
  pluginName: "Demo",
  page: {
    id: "settings",
    target: "desktop.main",
    entry: "dist/settings.html",
    title: "Demo settings",
  },
};

describe("desktop plugin page service", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("fetches a declared Desktop page with admin authentication", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          plugin_id: "demo.plugin",
          plugin_name: "Demo",
          page_id: "settings",
          target: "desktop.main",
          title: "Demo settings",
          html: "<main>hello</main>",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = await fetchDesktopPluginPage(
      {
        ...defaultGatewayConnectionSettings,
        adminBearerToken: "admin-secret",
      },
      activePage,
    );

    expect(result.html).toBe("<main>hello</main>");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:6185/api/plugins/demo.plugin/pages/settings",
      expect.objectContaining({
        method: "GET",
        headers: expect.any(Headers),
      }),
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((request.headers as Headers).get("Authorization")).toBe(
      "Bearer admin-secret",
    );
  });

  it("rejects a response for another host target", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          plugin_id: "demo.plugin",
          plugin_name: "Demo",
          page_id: "settings",
          target: "webui.admin",
          title: "Demo settings",
          html: "<main>hello</main>",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      fetchDesktopPluginPage(defaultGatewayConnectionSettings, activePage),
    ).rejects.toThrow("different target");
  });

  it("injects a locked-down CSP and read-only page context", () => {
    const html = sandboxPluginPageDocument({
      plugin_id: "demo.plugin",
      plugin_name: "Demo",
      page_id: "settings",
      target: "desktop.main",
      title: "Demo settings",
      html: "<head><title>Demo</title></head><body>hello</body>",
    });

    expect(html).toContain("default-src 'none'");
    expect(html).toContain("connect-src 'none'");
    expect(html).toContain("window.__NAHIDA_PLUGIN_CONTEXT__");
    expect(html).toContain("Object.freeze(value)");
    expect(html).toContain("<title>Demo</title>");
  });
});
