import { describe, expect, it } from "vitest";

import {
  sanitizeExpressionMap,
  sanitizeMotionMap,
} from "./modelMappingStorage";

describe("model mapping sanitization", () => {
  it("preserves ordered expression fallbacks while removing invalid values", () => {
    expect(
      sanitizeExpressionMap({
        happy: ["HappyA", "HappyB", "HappyA", null],
        cleared: [],
        "invalid keyword": ["ignored"],
      }),
    ).toEqual({
      happy: ["HappyA", "HappyB"],
      cleared: [],
    });
  });

  it("normalizes supported motion targets", () => {
    expect(
      sanitizeMotionMap({
        nod: { source: "procedural", motion: "nod" },
        wave: { source: "model", group: " Gesture ", index: 2 },
        notify: { source: "model", group: "Invalid", index: -1 },
      }),
    ).toEqual({
      nod: { source: "procedural", motion: "nod" },
      wave: { source: "model", group: "Gesture", index: 2 },
    });
  });
});
