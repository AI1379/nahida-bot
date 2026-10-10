import { describe, expect, it } from 'vitest';
import { fitAffineResponse } from './rig-probe.js';
const points = [-3, -1, 4, -2, 2, 5, -1, 3];
describe('rig geometry diagnostic', () => {
  it('separates a known rotation from translation', () => {
    const angle = Math.PI / 6;
    const moved = points.flatMap((x, i) => i % 2 ? [] : [
      x * Math.cos(angle) - points[i + 1] * Math.sin(angle) + 30,
      x * Math.sin(angle) + points[i + 1] * Math.cos(angle) - 17,
    ]);
    expect(fitAffineResponse(points, moved).rotationDegrees).toBeCloseTo(30);
  });
  it('does not mislabel a vertical shift as a body lean', () => {
    const moved = points.map((value, index) => value + (index % 2 ? 43 : 0));
    const result = fitAffineResponse(points, moved);
    expect(result.rotationDegrees).toBeCloseTo(0);
    expect(result.centroidShift[0]).toBeCloseTo(0);
    expect(result.centroidShift[1]).toBeCloseTo(43);
  });
  it('rejects a degenerate shape instead of reporting an invented angle', () => {
    expect(() => fitAffineResponse([0, 0, 1, 1, 2, 2], [0, 0, 1, 1, 2, 2])).toThrow('collinear');
  });
});
