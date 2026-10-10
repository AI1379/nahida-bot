import { describe, expect, it } from 'vitest';
import { readMtn, retargetNod, motion3FromSamples, readLinearMotion3, samplePoints } from './curves.js';

const sourceRanges = {
  PARAM_ANGLE_Y: { minimum: -30, maximum: 30, default: 0 },
  PARAM_ANGLE_Z: { minimum: -30, maximum: 30, default: 0 },
  PARAM_EYE_L_OPEN: { minimum: 0, maximum: 1.5, default: 1 },
};
const targetRanges = {
  ParamAngleY: { minimum: -30, maximum: 30, default: 0 },
  ParamAngleZ: { minimum: -30, maximum: 30, default: 0 },
  ParamEyeLOpen: { minimum: 0, maximum: 1.2, default: 1 },
};
const source = readMtn('$fps=10\nPARAM_ANGLE_Y=0,-10,-27,17,-9,-9,-9\nPARAM_ANGLE_Z=0,-8,-14,-14,-14,-14,-14\nPARAM_EYE_L_OPEN=1.5,1.125,0.75,0,0.75,1.5,1.5');

describe('calibrated Bandori nod experiment', () => {
  it('maps an authored open eye to target rest and retains fractional closure', () => {
    const motion = retargetNod(source, sourceRanges, targetRanges);
    expect(motion.curves.ParamEyeLOpen[0]).toBe(1);
    expect(motion.curves.ParamEyeLOpen[2]).toBeCloseTo(0.5);
    expect(motion.curves.ParamEyeLOpen[3]).toBe(0);
    expect(Math.max(...motion.curves.ParamEyeLOpen)).toBe(1);
  });

  it('retains the nod extrema timing but removes the final pose hold', () => {
    const motion = retargetNod(source, sourceRanges, targetRanges);
    expect(motion.curves.ParamAngleY[2]).toBeCloseTo(-27 * 0.55);
    expect(motion.curves.ParamAngleY[3]).toBeCloseTo(17 * 0.55);
    expect(motion.curves.ParamAngleZ[4]).toBeCloseTo(-14 * 0.25);
    for (const [id, samples] of Object.entries(motion.curves)) {
      expect(samples.at(-1)).toBe(targetRanges[id].default);
      expect(samples.every(value => value >= targetRanges[id].minimum && value <= targetRanges[id].maximum)).toBe(true);
    }
  });

  it('omits missing target channels instead of manufacturing bindings', () => {
    const motion = retargetNod(source, sourceRanges, { ParamAngleY: targetRanges.ParamAngleY });
    expect(Object.keys(motion.curves)).toEqual(['ParamAngleY']);
  });

  it('exports the preview samples unchanged, including its neutral endpoint', () => {
    const motion = retargetNod(source, sourceRanges, targetRanges);
    const document = motion3FromSamples(motion), imported = readLinearMotion3(document);
    expect(document.Meta.TotalPointCount).toBe(Object.keys(motion.curves).length * motion.count);
    for (const [id, samples] of Object.entries(motion.curves)) {
      samples.forEach((value, index) => expect(samplePoints(imported.curves[id], index / motion.fps)).toBeCloseTo(value));
    }
  });

  it('preserves one-value constant channels and rejects inconsistent timelines', () => {
    expect(readMtn('$fps=30\nPARAM_ANGLE_Y=0,1,2\nPARAM_ANGLE_X=0').count).toBe(3);
    expect(() => readMtn('$fps=30\nPARAM_ANGLE_Y=0,1,2\nPARAM_ANGLE_X=0,1')).toThrow('Unequal samples');
  });
});
