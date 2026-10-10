/** Small, lossless sample reader for the checked local MTN experiment. */
export function readMtn(text) {
  const curves = {};
  let fps = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const split = line.indexOf('=');
    if (split < 1) throw new Error('Invalid MTN line');
    const id = line.slice(0, split), value = line.slice(split + 1);
    if (id === '$fps') fps = Number(value);
    if (id.startsWith('$') || id === 'PARAM_IMPORT') continue;
    const samples = value.split(',').map(Number);
    if (!samples.length || samples.some(v => !Number.isFinite(v))) throw new Error(`Invalid samples: ${id}`);
    curves[id] = samples;
  }
  if (!Number.isFinite(fps) || fps <= 0) throw new Error('Invalid MTN frame rate');
  const count = Math.max(...Object.values(curves).map(samples => samples.length));
  if (!Number.isFinite(count)) throw new Error('Empty MTN');
  for (const [id, samples] of Object.entries(curves)) {
    if (samples.length !== 1 && samples.length !== count) throw new Error(`Unequal samples: ${id}`);
  }
  return { fps, duration: (count - 1) / fps, count, curves };
}

export function sampleCurve(samples, time, fps) {
  const frame = Math.max(0, Math.min(samples.length - 1, time * fps));
  const index = Math.floor(frame), next = Math.min(index + 1, samples.length - 1);
  return samples[index] + (samples[next] - samples[index]) * (frame - index);
}

/** The saved baseline contains linear segments; reject other encodings explicitly. */
export function readLinearMotion3(document) {
  const curves = {};
  for (const curve of document.Curves) {
    if (curve.Target !== 'Parameter') continue;
    const points = [{ time: curve.Segments[0], value: curve.Segments[1] }];
    for (let i = 2; i < curve.Segments.length; i += 3) {
      if (curve.Segments[i] !== 0) throw new Error('Baseline has non-linear segments');
      points.push({ time: curve.Segments[i + 1], value: curve.Segments[i + 2] });
    }
    curves[curve.Id] = points;
  }
  return { duration: document.Meta.Duration, curves };
}

export function samplePoints(points, time) {
  if (time <= points[0].time) return points[0].value;
  const right = points.findIndex(point => point.time >= time);
  if (right < 0) return points.at(-1).value;
  const left = points[right - 1], next = points[right];
  return left.value + (next.value - left.value) * (time - left.time) / (next.time - left.time);
}

export const parameterPairs = [
  ['PARAM_ANGLE_X', 'ParamAngleX', 'yaw'],
  ['PARAM_ANGLE_Y', 'ParamAngleY', 'pitch'],
  ['PARAM_ANGLE_Z', 'ParamAngleZ', 'roll'],
  ['PARAM_BODY_ANGLE_X', 'ParamBodyAngleX', 'body'],
  ['PARAM_BODY_ANGLE_Y', 'ParamBodyAngleY', 'body'],
  ['PARAM_BODY_ANGLE_Z', 'ParamBodyAngleZ', 'body'],
  ['PARAM_EYE_BALL_X', 'ParamEyeBallX', 'gaze'],
  ['PARAM_EYE_BALL_Y', 'ParamEyeBallY', 'gaze'],
  ['PARAM_EYE_L_OPEN', 'ParamEyeLOpen', 'eye'],
  ['PARAM_EYE_R_OPEN', 'ParamEyeROpen', 'eye'],
  ['PARAM_BROW_L_Y', 'ParamBrowLY', 'brow'],
  ['PARAM_BROW_R_Y', 'ParamBrowRY', 'brow'],
  ['PARAM_EYE_L_SMILE', 'ParamEyeLSmile', 'face'],
  ['PARAM_EYE_R_SMILE', 'ParamEyeRSmile', 'face'],
  ['PARAM_BROW_L_ANGLE', 'ParamBrowLAngle', 'face'],
  ['PARAM_BROW_R_ANGLE', 'ParamBrowRAngle', 'face'],
];

const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
const ease = (value) => { const x = clamp(value, 0, 1); return x * x * x * (10 + x * (-15 + 6 * x)); };
export const defaultCalibration = { pitch: 0.55, roll: 0.25, body: 0.4, recover: 1.5 };

/** Model-specific nod study. Keeps source timing; adds a separately tunable recovery. */
export function retargetNod(source, sourceRanges, targetRanges, config = defaultCalibration) {
  const recoveryEnd = config.recover + 0.75;
  const duration = recoveryEnd;
  const count = Math.ceil(duration * source.fps) + 1;
  const curves = {}, report = [];
  for (const [sourceId, targetId, group] of parameterPairs) {
    const samples = source.curves[sourceId], from = sourceRanges[sourceId], to = targetRanges[targetId];
    if (!samples || !from || !to) continue;
    const gain = config[group] ?? ({ yaw: 0.5, gaze: 0.5, brow: 0.65, face: 0.65, eye: 1 }[group]);
    const baseline = samples[0];
    const mapped = [];
    for (let i = 0; i < count; i++) {
      const time = i / source.fps, value = sampleCurve(samples, time, source.fps);
      // Source eyes begin at 1.5. Preserve the fraction of the authored open pose;
      // normal-open on the target is its resting value, not its numeric maximum.
      const delta = group === 'eye'
        ? (value / Math.max(baseline, 0.001) - 1) * (to.default - to.minimum)
        : (value - baseline) / Math.max((value >= baseline ? from.maximum - from.default : from.default - from.minimum), 0.001)
          * (value >= baseline ? to.maximum - to.default : to.default - to.minimum);
      const enter = ease(time / 0.15);
      const exit = 1 - ease((time - config.recover) / 0.75);
      mapped.push(clamp(to.default + delta * gain * enter * exit, to.minimum, to.maximum));
    }
    if (Math.max(...mapped) - Math.min(...mapped) < 0.00001) continue;
    curves[targetId] = mapped;
    report.push({ sourceId, targetId, group, gain, sourceBaseline: baseline, sourceRange: from, targetRange: to, minimum: Math.min(...mapped), maximum: Math.max(...mapped) });
  }
  return { fps: source.fps, count, duration: (count - 1) / source.fps, curves, report, config: { ...config } };
}

export function motion3FromSamples(motion) {
  const Curves = Object.entries(motion.curves).map(([Id, samples]) => ({
    Target: 'Parameter', Id,
    Segments: samples.flatMap((value, index) => index === 0 ? [0, value] : [0, index / motion.fps, value]),
  }));
  return {
    Version: 3,
    Meta: { Duration: motion.duration, Fps: motion.fps, Loop: false, AreBeziersRestricted: true,
      FadeInTime: 0.15, FadeOutTime: 0.2, CurveCount: Curves.length,
      TotalSegmentCount: Curves.length * (motion.count - 1), TotalPointCount: Curves.length * motion.count, UserDataCount: 0, TotalUserDataSize: 0 },
    Curves,
  };
}
