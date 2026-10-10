import * as PIXI from 'pixi.js';
import { Live2DModel } from 'pixi-live2d-display';
import { readMtn, sampleCurve, defaultCalibration, retargetNod, motion3FromSamples } from './curves.js';
import { verifyNativeMotion, recordComparison } from './verification.js';
import { probeRigParameters } from './rig-probe.js';
import { createFittingStudy, directMotion, fitParameterIds, textFingerprint } from './fitting-study.js';

window.PIXI = PIXI;
Live2DModel.registerTicker(PIXI.Ticker);
const sourceRoot = '/live2d_model/香澄-2023常服/';
const targetRoot = '/live2d_model/Nahida_1080/';
const motionNames = ['nod01', 'eeto01', 'idle01', 'surprised01', 'smile01', 'bye01'];
const motionFile = name => `${sourceRoot}live2d/chara/001_general_rip/${name}.mtn`;
const element = id => document.getElementById(id);
const stages = [], config = { ...defaultCalibration };
const gallery = document.querySelector('main');
// Cubism 4 shares renderer resources; use one WebGL context for both target models.
// Rendering is manual so pausing the experiment also stops continuous redraws.
const sharedApp = new PIXI.Application({ backgroundAlpha: 0, antialias: true, resolution: 1, preserveDrawingBuffer: true, autoStart: false });
gallery.style.position = 'relative';
Object.assign(sharedApp.view.style, { position: 'absolute', left: '0', pointerEvents: 'none' });
gallery.appendChild(sharedApp.view);
const referenceCanvas = document.createElement('canvas');
Object.assign(referenceCanvas.style, { position: 'absolute', left: '0', pointerEvents: 'none' });
gallery.appendChild(referenceCanvas);
const fittedResults = new Map();
let sourceMotion, baselineMotion, candidateMotion, fittingStudy, fittingResult;
let currentMotionName = 'nod01', busy = false, fitController;
let playing = false, time = 0, lastTime = 0;

async function readResource(url, format = 'json') {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status}: ${url}`);
  return format === 'json' ? response.json() : response.text();
}

/** Read the actual loaded Core model; display-info JSON does not include ranges. */
function parameterSnapshot(core, cubism2) {
  const parameters = {};
  if (cubism2) {
    const context = core.getModelContext();
    for (const id of Object.keys(sourceMotion.curves)) {
      const index = core.getParamIndex(id);
      const minimum = context.getParamMin(index), maximum = context.getParamMax(index);
      if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) continue;
      parameters[id] = { index, minimum, maximum, default: core.getParamFloat(index) };
    }
  } else {
    const ids = core._model.parameters.ids;
    for (let i = 0; i < ids.length; i++) {
      parameters[ids[i]] = { index: i, minimum: core.getParameterMinimumValue(i), maximum: core.getParameterMaximumValue(i), default: core.getParameterDefaultValue(i) };
    }
  }
  return parameters;
}

async function loadStage(id, entry, cubism2 = false) {
  const host = element(id);
  const app = sharedApp;
  const settings = await readResource(entry);
  settings.url = new URL(entry, location.href).href;
  if (cubism2) { settings.motions = {}; settings.expressions = []; }
  else { settings.FileReferences.Motions = {}; settings.FileReferences.Expressions = []; }
  const model = await Live2DModel.from(settings, { autoInteract: false, autoUpdate: false });
  app.stage.addChild(model);
  const internal = model.internalModel, core = internal.coreModel;
  const parameters = parameterSnapshot(core, cubism2);
  // Isolate authored input from the SDK's optional procedural movements.
  internal.eyeBlink = undefined;
  internal.updateFocus = () => {};
  internal.updateNaturalMovements = () => {};
  const physics = internal.physics;
  const sourcePhysicsData = cubism2 && settings.physics
    ? await readResource(new URL(settings.physics, settings.url).href)
    : null;
  const mask = new PIXI.Graphics();
  app.stage.addChild(mask);
  model.mask = mask;
  const setValue = (parameter, value) => cubism2
    ? core.setParamFloat(parameter.index, value)
    : core.setParameterValueByIndex(parameter.index, value);
  const stage = { id, app, model, internal, core, parameters, physics, setValue, values: {}, cubism2 };
  stage.resetPhysics = () => {
    // Cubism 2 PhysicsHair tracks absolute time and has no initialize method.
    // Recreate its small physics controller when scrubbing backwards.
    if (sourcePhysicsData) stage.physics = new physics.constructor(core, sourcePhysicsData);
    else stage.physics?.initialize?.();
  };
  internal.on('beforeMotionUpdate', () => {
    for (const parameter of Object.values(parameters)) setValue(parameter, parameter.default);
  });
  internal.on('afterMotionUpdate', () => {
    for (const [parameterId, value] of Object.entries(stage.values)) {
      if (parameters[parameterId]) setValue(parameters[parameterId], value);
    }
  });
  const nativeWidth = model.width, nativeHeight = model.height;
  function resize() {
    app.renderer.resize(gallery.clientWidth, host.clientHeight);
    app.view.style.top = `${host.getBoundingClientRect().top - gallery.getBoundingClientRect().top}px`;
    referenceCanvas.style.top = app.view.style.top;
    referenceCanvas.width = gallery.clientWidth;
    referenceCanvas.height = host.clientHeight;
    const detail = element('view').value === 'detail';
    const scale = Math.min(host.clientWidth / nativeWidth, host.clientHeight / nativeHeight) * (detail ? 1.8 : 1.15);
    model.scale.set(scale);
    model.anchor.set(0.5, 0);
    const left = host.getBoundingClientRect().left - gallery.getBoundingClientRect().left;
    model.position.set(left + host.clientWidth / 2, detail ? (cubism2 ? -125 : -15) : 8);
    mask.clear().beginFill(0xffffff).drawRect(left, 0, host.clientWidth, host.clientHeight).endFill();
    app.render();
  }
  stage.resize = resize;
  resize();
  new ResizeObserver(resize).observe(host);
  stages.push(stage);
  return stage;
}

function updateCandidate() {
  const source = stages.find(stage => stage.id === 'source');
  const target = stages.find(stage => stage.id === 'candidate');
  candidateMotion = retargetNod(sourceMotion, source.parameters, target.parameters, config);
  element('diagnostics').textContent = JSON.stringify({
    source: { file: motionFile(currentMotionName), duration: sourceMotion.duration, fps: sourceMotion.fps },
    candidate: { duration: candidateMotion.duration, calibration: config, mappings: candidateMotion.report },
    omittedSourceParameters: Object.keys(sourceMotion.curves).filter(id => !candidateMotion.report.some(mapping => mapping.sourceId === id)),
  }, null, 2);
}

function valuesAt(stage, at) {
  const mode = element('comparison').value;
  let motion = stage.id === 'source' ? sourceMotion : baselineMotion;
  if (stage.id === 'candidate' && mode === 'manual') motion = candidateMotion;
  if (stage.id === 'candidate' && mode === 'fitted' && fittingResult) motion = fittingResult.motion;
  const values = Object.fromEntries(Object.entries(motion.curves).map(([id, samples]) => [id, sampleCurve(samples, at, motion.fps)]));
  if (stage.id === 'candidate' && mode === 'ablation') delete values.ParamBodyAngleZ;
  return isolatedValues(values, stage.cubism2);
}

function isolatedValues(values, source) {
  const mode = element('isolation').value;
  if (mode === 'all') return values;
  const ids = mode === 'body-z'
    ? [source ? 'PARAM_BODY_ANGLE_Z' : 'ParamBodyAngleZ']
    : (source ? ['PARAM_ANGLE_X', 'PARAM_ANGLE_Y', 'PARAM_ANGLE_Z'] : ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ']);
  return Object.fromEntries(Object.entries(values).filter(([id]) => ids.includes(id)));
}

function updateComparisonLabels() {
  const mode = element('comparison').value;
  const labels = { ablation: ['诊断 · 去掉身体 Z', '与数值直转相同，只停用身体 Z'], manual: ['旧实验 · 手动调参', '上轮候选：未通过迁移保真验证'],
    fitted: fittingResult ? ['模型级联合拟合', '固定参照和求解器 · 保留源时序及末尾姿态'] : ['尚未拟合', '点击“拟合当前动作”生成候选'] };
  [element('candidate-title').textContent, element('candidate-description').textContent] = labels[mode];
  if (mode === 'fitted' && fittingResult?.report.reviewFlags?.length) element('candidate-description').textContent = reviewLabel(fittingResult.report.reviewFlags);
  element('download').disabled = busy || mode === 'ablation' || (mode === 'fitted' && !fittingResult);
  document.querySelector('.controls').hidden = mode !== 'manual';
  element('source-description').textContent = `${currentMotionName}.mtn · ${sourceMotion.duration.toFixed(2)} s · ${sourceMotion.count} 帧`;
}

function drawReferences() {
  const context = referenceCanvas.getContext('2d');
  context.clearRect(0, 0, referenceCanvas.width, referenceCanvas.height);
  if (!fittingStudy || !element('references').checked) return;
  for (const stage of stages) {
    const points = fittingStudy.points(stage), matrix = stage.model.worldTransform;
    Object.entries(points).forEach(([name, point], index) => {
      const x = matrix.a * point[0] + matrix.c * point[1] + matrix.tx;
      const y = matrix.b * point[0] + matrix.d * point[1] + matrix.ty;
      context.fillStyle = name.endsWith('Check') ? '#ed982c' : '#008e84';
      context.beginPath(); context.arc(x, y, 4, 0, Math.PI * 2); context.fill();
      context.font = '12px sans-serif'; context.fillText(String(index + 1), x + 6, y - 5);
    });
  }
}

function renderAt(at, deltaMs = 1000 / 60) {
  time = at;
  for (const stage of stages) {
    stage.values = valuesAt(stage, at);
    stage.internal.physics = element('physics').checked ? stage.physics : undefined;
    stage.internal.update(deltaMs, at * 1000);
    const prefix = stage.cubism2 ? 'PARAM_ANGLE_' : 'ParamAngle';
    const pitch = stage.values[`${prefix}Y`] ?? 0, roll = stage.values[`${prefix}Z`] ?? 0;
    const bodyZ = stage.values[stage.cubism2 ? 'PARAM_BODY_ANGLE_Z' : 'ParamBodyAngleZ'] ?? 0;
    element(`${stage.id}-values`).textContent = `参数：头 Y ${pitch.toFixed(2)} · 头 Z ${roll.toFixed(2)} · 身体 Z ${bodyZ.toFixed(2)}`;
  }
  sharedApp.render();
  drawReferences();
  element('seek').value = String(at);
  element('time').textContent = `${at.toFixed(2)} s`;
}

/** Deterministic scrubbing warms target physics from the start of the clip. */
function seek(at) {
  playing = false;
  for (const stage of stages) stage.resetPhysics();
  renderAt(0);
  for (let step = 1; step <= Math.floor(at * 60); step++) renderAt(step / 60);
  if (at * 60 % 1) renderAt(at, (at * 60 % 1) * 1000 / 60);
}

function animate(now) {
  const delta = lastTime ? Math.min(now - lastTime, 50) : 0;
  lastTime = now;
  if (playing) {
    let next = time + delta / 1000 * Number(element('speed').value);
    if (next > sourceMotion.duration + 0.6) {
      next = element('loop').checked ? 0 : sourceMotion.duration + 0.6;
      if (!element('loop').checked) playing = false;
    }
    renderAt(next, delta * Number(element('speed').value));
  }
  requestAnimationFrame(animate);
}

function download() {
  const selected = element('comparison').value === 'fitted' ? fittingResult.motion : candidateMotion;
  const blob = new Blob([JSON.stringify(motion3FromSamples(selected), null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = `nahida-kasumi-${currentMotionName}-${element('comparison').value}.motion3.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function setBusy(value) {
  busy = value; playing = false;
  document.querySelectorAll('button,select,input').forEach(control => { control.disabled = value; });
  element('cancel-fit').disabled = !value;
  updateComparisonLabels();
}

function renderResults() {
  const percent = value => value === null ? '—' : `${(value * 100).toFixed(1)}%`;
  element('fit-results').replaceChildren(...Array.from(fittedResults, ([name, result]) => {
    const row = document.createElement('tr'), summary = result.report.summary;
    const values = [name, percent(summary.fitImprovement), percent(summary.checkImprovement),
      `${summary.baselineBodyRollRmsDegrees.toFixed(2)} → ${summary.fittedBodyRollRmsDegrees.toFixed(2)}`,
      `${summary.saturatedFrames}/${result.motion.count}`, `${(result.report.elapsedMs / 1000).toFixed(1)} s`, reviewLabel(result.report.reviewFlags ?? [])];
    if (result.report.reviewFlags?.length) row.className = 'review-warning';
    values.forEach(value => { const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell); });
    return row;
  }));
}

function reviewLabel(flags) {
  if (flags.includes('check-point-regression')) return '检查点退化 · 不建议采用';
  if (flags.includes('control-bound-hit')) return '参数触界 · 需检查可达性';
  return '仅几何改善 · 待视觉复核';
}

async function loadSavedResult(name, source) {
  try {
    const response = await fetch(`/live2d_model/_motion-lab/fitted/${name}.study.json`);
    if (!response.ok) return null;
    const result = await response.json();
    if (result.report?.configurationFingerprint !== fittingStudy.configurationFingerprint || result.report?.sourceFileHash !== source.sourceFileHash) return null;
    if (result.motion?.count !== source.count || result.motion?.fps !== source.fps || result.motion?.duration !== source.duration || !Array.isArray(result.report.reviewFlags)) return null;
    if (!fitParameterIds.every(id => Array.isArray(result.motion.curves?.[id]))) return null;
    const valid = Object.entries(result.motion.curves).every(([id, samples]) => {
      const range = stages[2].parameters[id];
      return range && Array.isArray(samples) && samples.length === source.count
        && samples.every(v => Number.isFinite(v) && v >= range.minimum && v <= range.maximum);
    });
    return valid ? result : null;
  } catch { return null; }
}

async function selectMotion(name) {
  const text = await readResource(motionFile(name), 'text');
  sourceMotion = { ...readMtn(text), sourceFileHash: await textFingerprint(text) };
  currentMotionName = name;
  baselineMotion = directMotion(sourceMotion, stages[0].parameters, stages[2].parameters);
  fittingResult = fittedResults.get(name) ?? await loadSavedResult(name, sourceMotion);
  if (fittingResult) { fittedResults.set(name, fittingResult); renderResults(); }
  element('seek').max = String(sourceMotion.duration + 0.6);
  element('motion-name').value = name;
  element('comparison').value = 'fitted';
  element('comparison').querySelector('[value="manual"]').disabled = name !== 'nod01';
  updateCandidate(); updateComparisonLabels(); seek(0);
}

async function fitCurrent(signal) {
  fittingResult = await fittingStudy.fit(sourceMotion, { baseline: baselineMotion, signal,
    onProgress: (frame, total) => { element('status').textContent = `拟合 ${currentMotionName}：${frame}/${total} 帧`; } });
  fittingResult.report.sourceFile = motionFile(currentMotionName);
  fittedResults.set(currentMotionName, fittingResult);
  element('comparison').value = 'fitted'; renderResults();
}

async function runFitBatch(names) {
  if (busy) return;
  fitController = new AbortController(); setBusy(true);
  try {
    for (const name of names) { await selectMotion(name); await fitCurrent(fitController.signal); }
    element('status').textContent = `完成 ${names.length} 个动作 · 结果仅为局部几何拟合，需视觉复核`;
  } catch (error) {
    element('status').textContent = error.name === 'AbortError' ? '已取消拟合' : `拟合失败：${error.message}`;
    if (error.name !== 'AbortError') console.error(error);
  } finally { setBusy(false); seek(0); }
}

async function start() {
  sourceMotion = readMtn(await readResource(motionFile('nod01'), 'text'));
  await loadStage('source', `${sourceRoot}model.json`, true);
  await loadStage('old', `${targetRoot}Nahida_1080.model3.json`);
  await loadStage('candidate', `${targetRoot}Nahida_1080.model3.json`);
  fittingStudy = await createFittingStudy(stages[0], stages[2]);
  await selectMotion('nod01');
  setBusy(false);
  element('play').onclick = () => { playing = !playing; };
  element('restart').onclick = () => { seek(0); playing = true; };
  element('seek').oninput = () => seek(Number(element('seek').value));
  element('physics').onchange = () => seek(time);
  element('isolation').onchange = () => seek(time);
  element('comparison').onchange = () => { updateComparisonLabels(); seek(time); };
  updateComparisonLabels();
  element('view').onchange = () => stages.forEach(stage => stage.resize());
  element('download').onclick = download;
  element('fit-current').onclick = () => runFitBatch([currentMotionName]);
  element('fit-batch').onclick = () => runFitBatch(motionNames);
  element('cancel-fit').onclick = () => fitController?.abort();
  element('motion-name').onchange = async () => {
    setBusy(true);
    try { await selectMotion(element('motion-name').value); }
    catch (error) { element('status').textContent = error.message; }
    finally { setBusy(false); }
  };
  element('references').onchange = drawReferences;
  document.querySelectorAll('[data-time]').forEach(button => {
    button.onclick = () => seek(button.dataset.time === 'end' ? sourceMotion.duration : Number(button.dataset.time));
  });
  for (const id of ['pitch', 'roll', 'body', 'recover']) element(id).oninput = () => {
    config[id] = Number(element(id).value);
    element(id).nextElementSibling.textContent = config[id].toFixed(2);
    updateCandidate(); seek(time);
  };
  // Development-only instrumentation for range extraction, frame checks and exports.
  window.motionStudy = {
    seek, renderAt, stages,
    get source() { return sourceMotion; }, get candidate() { return fittingResult?.motion ?? candidateMotion; },
    get results() { return Object.fromEntries(fittedResults); }, get references() { return fittingStudy.profile; },
    fitBatch: runFitBatch, selectMotion,
    exportMotion: () => motion3FromSamples(fittingResult?.motion ?? candidateMotion),
    verifyNative: () => {
      const motion = fittingResult?.motion ?? candidateMotion;
      const result = verifyNativeMotion(stages.find(stage => stage.id === 'candidate'), motion3FromSamples(motion), motion);
      seek(time);
      return result;
    },
    record: () => recordComparison({ canvas: sharedApp.view, seek, renderAt, duration: sourceMotion.duration + 0.6,
      rightTitle: element('candidate-title').textContent, rightSubtitle: element('candidate-description').textContent }),
    probeRigs: () => {
      const source = stages.find(stage => stage.id === 'source'), target = stages.find(stage => stage.id === 'old');
      const result = {
        source: probeRigParameters(source, ['PARAM_BODY_ANGLE_Z', 'PARAM_BODY_ANGLE_Y', 'PARAM_UPPER_BODY', 'PARAM_ANGLE_Z', 'PARAM_ANGLE_Y']),
        target: probeRigParameters(target, ['ParamBodyAngleZ', 'ParamBodyAngleY', 'ParamAngleZ', 'ParamAngleY']),
        method: 'Unweighted affine fit over all mesh vertices, physics disabled. Diagnostic only, not a semantic pose or quality score.',
      };
      seek(time);
      return result;
    },
    setConfig: values => { Object.assign(config, values); updateCandidate(); seek(time); },
    play: () => { playing = true; }, pause: () => { playing = false; },
  };
  element('status').textContent = fittingResult ? '已恢复指纹匹配的保存结果 · 可直接回放或重新拟合' : '已加载固定模型参照 · 请选择当前动作拟合，或运行六动作验证';
  requestAnimationFrame(animate);
}

start().catch(error => {
  element('status').textContent = `加载失败：${error.message}`;
  console.error(error);
});
