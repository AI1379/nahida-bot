import assert from "node:assert/strict";
import { test } from "node:test";
import vm from "node:vm";
import {
  pageBridgeBootstrap,
  parsePageRequest,
} from "../src/features/plugins/pageBridge.ts";

const valid = {
  type: "nahida.plugin.page.request",
  session: "page-session",
  id: "1",
  action: "list",
  payload: {},
};

test("page requests are confined to the active frame session and bounded action data", () => {
  assert.equal(parsePageRequest(valid, "page-session")?.action, "list");
  assert.equal(parsePageRequest(valid, "another-page"), null);
  assert.equal(
    parsePageRequest({ ...valid, action: "../../config" }, "page-session"),
    null,
  );
  assert.equal(
    parsePageRequest({ ...valid, payload: [] }, "page-session"),
    null,
  );
  assert.equal(
    parsePageRequest(
      { ...valid, payload: { value: "x".repeat(65537) } },
      "page-session",
    ),
    null,
  );
});

test("sandbox bridge accepts replies only from its parent and matching session", async () => {
  let receive;
  let sent;
  const parent = {
    postMessage: (message) => {
      sent = message;
    },
  };
  const window = {
    parent,
    addEventListener: (_type, handler) => {
      receive = handler;
    },
  };
  vm.runInNewContext(pageBridgeBootstrap("page-session"), {
    window,
    setTimeout,
    clearTimeout,
  });
  const result = window.nahidaPlugin.request("list", { query: "开心" });
  assert.equal(sent.action, "list");
  receive({
    source: {},
    data: {
      ...sent,
      type: "nahida.plugin.page.response",
      result: { total: 99 },
    },
  });
  receive({
    source: parent,
    data: {
      ...sent,
      type: "nahida.plugin.page.response",
      result: { total: 2 },
    },
  });
  assert.equal((await result).total, 2);
  assert.equal(Object.isFrozen(window.nahidaPlugin), true);
});
