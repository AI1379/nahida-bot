import { describe, expect, it } from "vitest";

import { petWindowPosition } from "./petWindow";

const workArea = { x: 100, y: 50, width: 1600, height: 900 };
const windowSize = { width: 420, height: 620 };

describe("petWindowPosition", () => {
  it("keeps only the configured strip visible while hidden", () => {
    expect(
      petWindowPosition(workArea, windowSize, "right", "hidden", 42),
    ).toEqual({ x: 1658, y: 330 });
    expect(
      petWindowPosition(workArea, windowSize, "left", "hidden", 42),
    ).toEqual({ x: -278, y: 330 });
  });

  it("places visible states inside each screen edge", () => {
    expect(
      petWindowPosition(workArea, windowSize, "right", "emerged", 42),
    ).toEqual({ x: 1280, y: 330 });
    expect(
      petWindowPosition(workArea, windowSize, "bottom", "chat", 42),
    ).toEqual({ x: 1280, y: 330 });
    expect(
      petWindowPosition(workArea, windowSize, "top", "speaking", 42),
    ).toEqual({ x: 1280, y: 50 });
  });

  it("exposes more of the window in peek state", () => {
    expect(
      petWindowPosition(workArea, windowSize, "right", "peek", 42),
    ).toEqual({ x: 1574, y: 330 });
  });

  it("returns integer physical coordinates at fractional DPI scales", () => {
    expect(
      petWindowPosition(
        { x: 0, y: 0, width: 2560, height: 1440 },
        { width: 525, height: 775 },
        "right",
        "hidden",
        42 * 1.25,
      ),
    ).toEqual({ x: 2508, y: 665 });
  });
});
