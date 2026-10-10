/** Shared local measurements; these are screen-space features, not inferred 3D joints. */
export const featureNames = ['bodyRoll', 'bodyX', 'bodyY', 'bodyWidth', 'bodyHeight', 'headRoll', 'headX', 'headY', 'headWidth', 'headHeight'];
export const featureWeights = [4, 0.3, 0.3, 0.4, 0.3, 3, 0.8, 1, 0.7, 1];
const subtract = (a, b) => [a[0] - b[0], a[1] - b[1]];
const midpoint = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1];
const length = a => Math.hypot(...a);
const angle = a => Math.atan2(a[1], a[0]);
const wrapAngle = a => Math.atan2(Math.sin(a), Math.cos(a));

export function readLandmarks(stage, bindings) {
  const meshes = new Map();
  return Object.fromEntries(Object.entries(bindings).map(([name, binding]) => {
    if (!meshes.has(binding.mesh)) {
      const index = stage.internal.getDrawableIndex(binding.mesh);
      if (index < 0) throw new Error(`Landmark mesh missing: ${binding.mesh}`);
      meshes.set(binding.mesh, stage.internal.getDrawableVertices(index));
    }
    const vertices = meshes.get(binding.mesh);
    if (vertices.length !== binding.vertexCount * 2) throw new Error(`Model changed: ${binding.mesh}`);
    const point = [0, 0];
    binding.triangle.forEach((vertex, index) => {
      point[0] += vertices[vertex * 2] * binding.weights[index];
      point[1] += vertices[vertex * 2 + 1] * binding.weights[index];
    });
    if (point.some(v => !Number.isFinite(v))) throw new Error(`Invalid landmark: ${name}`);
    return [name, point];
  }));
}

function frame(points) {
  const shoulder = subtract(points.shoulderRight, points.shoulderLeft);
  const face = subtract(points.faceRight, points.faceLeft);
  const bodyOrigin = midpoint(points.shoulderLeft, points.shoulderRight);
  const headOrigin = midpoint(points.faceLeft, points.faceRight);
  const bodyWidth = length(shoulder), headWidth = length(face);
  const bodyHeight = length(subtract(points.waist, bodyOrigin));
  const headHeight = length(subtract(points.chin, headOrigin));
  if (Math.min(bodyWidth, bodyHeight, headWidth, headHeight) < 1e-6) throw new Error('Degenerate reference pose');
  return { bodyOrigin, headOrigin, bodyWidth, bodyHeight, headWidth, headHeight,
    bodyAngle: angle(shoulder), headAngle: angle(face), bodyX: shoulder.map(v => v / bodyWidth) };
}

function headPosition(current, reference) {
  const delta = subtract(current.headOrigin, current.bodyOrigin);
  return [dot(delta, current.bodyX) / reference.headWidth,
    dot(delta, [-current.bodyX[1], current.bodyX[0]]) / reference.headHeight];
}

export function motionFeatures(points, referencePoints) {
  const current = frame(points), reference = frame(referencePoints);
  const bodyShift = subtract(current.bodyOrigin, reference.bodyOrigin);
  const head = headPosition(current, reference), restHead = headPosition(reference, reference);
  return [
    wrapAngle(current.bodyAngle - reference.bodyAngle),
    dot(bodyShift, reference.bodyX) / reference.bodyHeight,
    dot(bodyShift, [-reference.bodyX[1], reference.bodyX[0]]) / reference.bodyHeight,
    current.bodyWidth / reference.bodyWidth - 1,
    current.bodyHeight / reference.bodyHeight - 1,
    wrapAngle(current.headAngle - current.bodyAngle - reference.headAngle + reference.bodyAngle),
    head[0] - restHead[0], head[1] - restHead[1],
    current.headWidth / reference.headWidth - 1,
    current.headHeight / reference.headHeight - 1,
  ];
}

/** These two interior points are excluded from fitting; retain a separate check. */
export function checkFeatures(points, referencePoints) {
  const reference = frame(referencePoints);
  return ['noseCheck', 'chestCheck'].flatMap((name, index) => {
    const delta = subtract(points[name], referencePoints[name]);
    const scale = index === 0 ? reference.headWidth : reference.bodyHeight;
    return delta.map(value => value / scale);
  });
}

export function featureError(actual, expected, weights = actual.map(() => 1)) {
  const weighted = actual.reduce((sum, value, i) => sum + weights[i] * (value - expected[i]) ** 2, 0);
  return Math.sqrt(weighted / weights.reduce((a, b) => a + b, 0));
}
