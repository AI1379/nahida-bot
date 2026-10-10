import { describe, expect, it } from 'vitest';
import { fitControls, solveLinear } from './inverse-fit.js';
import { checkFeatures, motionFeatures, readLandmarks } from './landmark-features.js';
import { reviewFlags } from './fitting-study.js';

const reference = { shoulderLeft: [-1, 0], shoulderRight: [1, 0], waist: [0, 3], faceLeft: [-0.5, -2], faceRight: [0.5, -2], chin: [0, -1], noseCheck: [0, -1.6], chestCheck: [0, 1] };

describe('model-level inverse fitting', () => {
  it('flags degraded unused check points even when fitting error improves', () => {
    expect(reviewFlags({ baselineError: 0.1, fittedError: 0.01, baselineCheckError: 0.06, fittedCheckError: 0.074, saturatedFrames: 0 })).toEqual(['check-point-regression']);
    expect(reviewFlags({ baselineCheckError: 0.08, fittedCheckError: 0.06, saturatedFrames: 8 })).toEqual(['control-bound-hit']);
  });
  it('recovers coupled controls instead of applying independent gains', () => {
    const result = fitControls(q => [q[0] + q[1], 2 * q[0] - q[1]], [0.5, 0.1], [0, 0], [1, 1], { regularization: 0, temporalWeight: 0 });
    expect(result.controls[0]).toBeCloseTo(0.2, 5);
    expect(result.controls[1]).toBeCloseTo(0.3, 5);
  });
  it('reports an unreachable target at the control bound', () => {
    const result = fitControls(q => q, [2], [0], [1], { regularization: 0, temporalWeight: 0 });
    expect(result.controls).toEqual([1]);
    expect(result.saturated).toEqual([0]);
    expect(result.features[0]).toBeLessThan(2);
  });
  it('uses finite differences on a coupled nonlinear forward model', () => {
    const forward = q => [q[0], q[0] * q[1], q[1] ** 2];
    const desired = forward([0.4, -0.2]);
    const result = fitControls(forward, desired, [0.1, -0.1], [1, 1, 1], { iterations: 15, regularization: 0, temporalWeight: 0 });
    result.features.forEach((value, i) => expect(value).toBeCloseTo(desired[i], 4));
  });
  it('returns no fabricated inverse for a singular linear system', () => {
    expect(solveLinear([[1, 1], [2, 2]], [1, 2])).toBeNull();
  });
});

describe('local semantic references', () => {
  it('separates whole-body roll from head-relative roll', () => {
    const angle = 0.2;
    const rotated = Object.fromEntries(Object.entries(reference).map(([name, [x, y]]) => [name, [x * Math.cos(angle) - y * Math.sin(angle), x * Math.sin(angle) + y * Math.cos(angle)]]));
    const features = motionFeatures(rotated, reference);
    expect(features[0]).toBeCloseTo(angle);
    expect(features[5]).toBeCloseTo(0);
    expect(features[6]).toBeCloseTo(0);
    expect(features[7]).toBeCloseTo(0);
  });
  it('does not use the two check landmarks to optimize its fitting features', () => {
    const changed = { ...reference, noseCheck: [0.1, -1.3], chestCheck: [0.2, 1.2] };
    expect(motionFeatures(changed, reference)).toEqual(motionFeatures(reference, reference));
    expect(checkFeatures(changed, reference).some(v => Math.abs(v) > 0)).toBe(true);
  });
  it('binds a landmark to a triangle and rejects changed topology', () => {
    const bindings = { point: { mesh: 'skin', vertexCount: 3, triangle: [0, 1, 2], weights: [0.25, 0.5, 0.25] } };
    const stage = { internal: { getDrawableIndex: () => 0, getDrawableVertices: () => [0, 0, 3, 0, 0, 2] } };
    expect(readLandmarks(stage, bindings).point).toEqual([1.5, 0.5]);
    expect(() => readLandmarks(stage, { point: { ...bindings.point, vertexCount: 4 } })).toThrow('Model changed');
  });
});
