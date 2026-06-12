import { describe, expect, it } from "vitest";

import {
  distanceToRect,
  intersectRects,
  proximityIntent,
} from "./petProximity";

const workArea = { x: 0, y: 0, width: 1920, height: 1080 };
// Right-edge window mostly off-screen: 42px strip visible.
const hiddenRect = { x: 1878, y: 460, width: 420, height: 620 };
// Fully on-screen window.
const emergedRect = { x: 1500, y: 460, width: 420, height: 620 };

const thresholds = { wakeDistancePx: 96, hideDistancePx: 220 };

describe("intersectRects", () => {
  it("clips the off-screen part of the window", () => {
    expect(intersectRects(hiddenRect, workArea)).toEqual({
      x: 1878,
      y: 460,
      width: 42,
      height: 620,
    });
  });

  it("returns null when the window is fully off-screen", () => {
    expect(
      intersectRects({ x: 1920, y: 0, width: 420, height: 620 }, workArea),
    ).toBeNull();
  });
});

describe("distanceToRect", () => {
  it("is zero inside the rect and euclidean outside", () => {
    expect(distanceToRect({ x: 1890, y: 700 }, hiddenRect)).toBe(0);
    expect(
      distanceToRect({ x: 1878 - 30, y: 700 }, hiddenRect),
    ).toBe(30);
    expect(
      distanceToRect({ x: 1878 - 30, y: 460 - 40 }, hiddenRect),
    ).toBe(50);
  });
});

describe("proximityIntent", () => {
  it("peeks when the cursor approaches the hidden strip", () => {
    expect(
      proximityIntent(
        "hidden",
        { x: 1800, y: 700 },
        hiddenRect,
        workArea,
        thresholds,
      ),
    ).toBe("peek");
    expect(
      proximityIntent(
        "hidden",
        { x: 900, y: 500 },
        hiddenRect,
        workArea,
        thresholds,
      ),
    ).toBeNull();
  });

  it("emerges when the cursor touches the peeking pet", () => {
    expect(
      proximityIntent(
        "peek",
        { x: 1890, y: 700 },
        hiddenRect,
        workArea,
        thresholds,
      ),
    ).toBe("emerge");
  });

  it("hides a peeking pet once the cursor moves far away", () => {
    expect(
      proximityIntent(
        "peek",
        { x: 1500, y: 700 },
        hiddenRect,
        workArea,
        thresholds,
      ),
    ).toBe("hide");
    expect(
      proximityIntent(
        "peek",
        { x: 1820, y: 700 },
        hiddenRect,
        workArea,
        thresholds,
      ),
    ).toBeNull();
  });

  it("reports activity while the cursor hovers a visible pet", () => {
    for (const status of ["emerged", "speaking", "chat"] as const) {
      expect(
        proximityIntent(
          status,
          { x: 1600, y: 700 },
          emergedRect,
          workArea,
          thresholds,
        ),
      ).toBe("activity");
      expect(
        proximityIntent(
          status,
          { x: 900, y: 500 },
          emergedRect,
          workArea,
          thresholds,
        ),
      ).toBeNull();
    }
  });

  it("stays quiet during transitions and error state", () => {
    for (const status of ["emerging", "retreating", "error"] as const) {
      expect(
        proximityIntent(
          status,
          { x: 1600, y: 700 },
          emergedRect,
          workArea,
          thresholds,
        ),
      ).toBeNull();
    }
  });
});
