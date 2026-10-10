import profile from './model-landmarks.json';
import { parameterPairs, sampleCurve } from './curves.js';
import { readLandmarks, motionFeatures, checkFeatures, featureWeights, featureNames, featureError } from './landmark-features.js';
import { fitControls } from './inverse-fit.js';

export const fitParameterIds = ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamBodyAngleX', 'ParamBodyAngleY', 'ParamBodyAngleZ'];
const solverSettings = Object.freeze({ iterations: 6, regularization: 0.00005, temporalWeight: 0.0001 });
const atFrame = (motion, frame) => Object.fromEntries(Object.entries(motion.curves).map(([id, values]) => [id, sampleCurve(values, frame / motion.fps, motion.fps)]));

function withPose(stage, values) {
  stage.values = values;
  stage.internal.physics = undefined;
  stage.internal.update(1000 / 30, 0);
}

function reference(stage, bindings) {
  withPose(stage, {});
  return readLandmarks(stage, bindings);
}

function controlsToPose(stage, controls) {
  return Object.fromEntries(fitParameterIds.map((id, index) => {
    const range = stage.parameters[id], value = controls[index];
    const span = value >= 0 ? range.maximum - range.default : range.default - range.minimum;
    return [id, range.default + value * span];
  }));
}

/** Baseline only. The same fallback facial channels are kept in the fitted output. */
export function directMotion(source, sourceRanges, targetRanges) {
  const curves = {};
  for (const [sourceId, targetId, group] of parameterPairs) {
    const values = source.curves[sourceId], from = sourceRanges[sourceId], to = targetRanges[targetId];
    if (!values || !from || !to) continue;
    curves[targetId] = Array.from({ length: source.count }, (_, frame) => {
      const value = sampleCurve(values, frame / source.fps, source.fps);
      if (group === 'eye') return Math.max(to.minimum, Math.min(to.maximum, value / from.maximum * to.default));
      const delta = value - from.default;
      const span = delta >= 0 ? from.maximum - from.default : from.default - from.minimum;
      const normalized = span > 0 ? Math.max(-1, Math.min(1, delta / span)) : 0;
      return to.default + normalized * (normalized >= 0 ? to.maximum - to.default : to.default - to.minimum);
    });
  }
  return { fps: source.fps, count: source.count, duration: source.duration, curves };
}

async function fingerprint(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

export const textFingerprint = text => fingerprint(new TextEncoder().encode(text));

async function sha256(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Model fingerprint read failed: ${url}`);
  return fingerprint(await response.arrayBuffer());
}

/** Neither low fitting error nor an empty warning list grants visual acceptance. */
export function reviewFlags(summary) {
  const flags = [];
  if (summary.fittedCheckError > summary.baselineCheckError + 1e-9) flags.push('check-point-regression');
  if (summary.saturatedFrames > 0) flags.push('control-bound-hit');
  return flags;
}

function evaluate(context, controls, extras) {
  withPose(context.targetStage, { ...extras, ...controlsToPose(context.targetStage, controls) });
  return motionFeatures(readLandmarks(context.targetStage, profile.target.points), context.targetReference);
}

function fitFrame(context, source, baseline, index, previous) {
  const { sourceStage, targetStage, sourceReference, targetReference } = context;
  withPose(sourceStage, atFrame(source, index));
  const points = readLandmarks(sourceStage, profile.source.points);
  const desired = motionFeatures(points, sourceReference), checks = checkFeatures(points, sourceReference);
  const basePose = atFrame(baseline, index);
  withPose(targetStage, basePose);
  const basePoints = readLandmarks(targetStage, profile.target.points);
  const baseFeatures = motionFeatures(basePoints, targetReference);
  const result = fitControls(q => evaluate(context, q, basePose), desired, previous, featureWeights, { ...solverSettings, previous });
  const fittedFeatures = evaluate(context, result.controls, basePose);
  const fittedPoints = readLandmarks(targetStage, profile.target.points);
  return { controls: result.controls, pose: controlsToPose(targetStage, result.controls),
    record: { atMs: index / source.fps * 1000, desired, baseline: baseFeatures, fitted: fittedFeatures,
      baselineError: featureError(baseFeatures, desired, featureWeights), fittedError: featureError(fittedFeatures, desired, featureWeights),
      baselineCheckError: featureError(checkFeatures(basePoints, targetReference), checks),
      fittedCheckError: featureError(checkFeatures(fittedPoints, targetReference), checks),
      baselineBodyRollError: baseFeatures[0] - desired[0], fittedBodyRollError: fittedFeatures[0] - desired[0],
      saturated: result.saturated.map(i => fitParameterIds[i]) } };
}

function summarize(frames) {
  const mean = name => frames.reduce((sum, frame) => sum + frame[name], 0) / frames.length;
  const rms = name => Math.sqrt(frames.reduce((sum, frame) => sum + frame[name] ** 2, 0) / frames.length);
  const baselineError = mean('baselineError'), fittedError = mean('fittedError');
  const baselineCheckError = mean('baselineCheckError'), fittedCheckError = mean('fittedCheckError');
  return { baselineError, fittedError, baselineCheckError, fittedCheckError,
    fitImprovement: baselineError > 1e-8 ? 1 - fittedError / baselineError : null,
    checkImprovement: baselineCheckError > 1e-8 ? 1 - fittedCheckError / baselineCheckError : null,
    baselineBodyRollRmsDegrees: rms('baselineBodyRollError') * 180 / Math.PI,
    fittedBodyRollRmsDegrees: rms('fittedBodyRollError') * 180 / Math.PI,
    saturatedFrames: frames.filter(frame => frame.saturated.length).length };
}

export async function createFittingStudy(sourceStage, targetStage) {
  const [sourceHash, targetHash] = await Promise.all([
    sha256('/live2d_model/香澄-2023常服/live2d/chara/001_casual-2023_rip/kasumi_casual-2023.moc'),
    sha256('/live2d_model/Nahida_1080/Nahida_1080.moc3'),
  ]);
  if (sourceHash !== profile.source.modelSha256 || targetHash !== profile.target.modelSha256) throw new Error('Model fingerprint changed; reference bindings must be reviewed');
  for (const id of fitParameterIds) if (!targetStage.parameters[id]) throw new Error(`Target control missing: ${id}`);
  const sourceReference = reference(sourceStage, profile.source.points);
  const targetReference = reference(targetStage, profile.target.points);
  const context = { sourceStage, targetStage, sourceReference, targetReference };
  const algorithmVersion = 'local-landmark-inverse-v1';
  const configurationFingerprint = await textFingerprint(JSON.stringify({ algorithmVersion, profile, featureNames, featureWeights, solverSettings }));

  async function fit(source, options = {}) {
    const started = performance.now(), baseline = options.baseline ?? directMotion(source, sourceStage.parameters, targetStage.parameters);
    const curves = structuredClone(baseline.curves), frames = [];
    for (const id of fitParameterIds) curves[id] = [];
    let previous = fitParameterIds.map(() => 0);
    try {
      for (let index = 0; index < source.count; index++) {
        if (options.signal?.aborted) throw new DOMException('Fitting cancelled', 'AbortError');
        const result = fitFrame(context, source, baseline, index, previous);
        for (const id of fitParameterIds) curves[id].push(result.pose[id]);
        previous = result.controls;
        frames.push(result.record);
        if (index % 4 === 0) {
          options.onProgress?.(index + 1, source.count);
          await new Promise(resolve => setTimeout(resolve, 0));
        }
      }
      const summary = summarize(frames);
      return { motion: { fps: source.fps, count: source.count, duration: source.duration, curves }, baseline,
        report: { profileId: profile.id, algorithmVersion, configurationFingerprint, sourceFileHash: source.sourceFileHash,
          solverSettings, featureNames, featureWeights, summary, reviewFlags: reviewFlags(summary), frames,
          elapsedMs: performance.now() - started, modelFingerprints: { source: sourceHash, target: targetHash },
          status: 'experimental-awaiting-visual-review', fittedControls: fitParameterIds,
          unboundSourceParameters: Object.keys(source.curves).filter(id => !sourceStage.parameters[id]),
          limitations: ['2D local references only; not 3D joint reconstruction', 'Only six head/body controls fitted', 'Unreachable source motion may leave residual error', 'Face channels use the same uncalibrated baseline in both target previews', 'Physics disabled during fitting'] } };
    } finally {
      withPose(sourceStage, {}); withPose(targetStage, {});
    }
  }

  return { fit, profile, configurationFingerprint, sourceReference, targetReference,
    points: stage => readLandmarks(stage, stage.cubism2 ? profile.source.points : profile.target.points) };
}
