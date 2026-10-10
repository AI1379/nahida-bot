/** Fit a 2D affine response between corresponding vertices of ONE model. */
export function fitAffineResponse(before, after) {
  if (before.length !== after.length || before.length < 6 || before.length % 2) {
    throw new Error('Expected matching non-degenerate 2D vertex arrays');
  }
  const count = before.length / 2;
  const center = values => {
    let x = 0, y = 0;
    for (let i = 0; i < values.length; i += 2) { x += values[i]; y += values[i + 1]; }
    return [x / count, y / count];
  };
  const [x, y] = center(before), [nextX, nextY] = center(after);
  let xx = 0, xy = 0, yy = 0, xX = 0, yX = 0, xY = 0, yY = 0, squaredDisplacement = 0;
  for (let i = 0; i < before.length; i += 2) {
    const dx = before[i] - x, dy = before[i + 1] - y;
    const dX = after[i] - nextX, dY = after[i + 1] - nextY;
    xx += dx * dx; xy += dx * dy; yy += dy * dy;
    xX += dx * dX; yX += dy * dX; xY += dx * dY; yY += dy * dY;
    squaredDisplacement += (after[i] - before[i]) ** 2 + (after[i + 1] - before[i + 1]) ** 2;
  }
  const determinant = xx * yy - xy * xy;
  if (Math.abs(determinant) < 1e-10) throw new Error('Cannot fit collinear vertices');
  const a = (xX * yy - yX * xy) / determinant, b = (yX * xx - xX * xy) / determinant;
  const c = (xY * yy - yY * xy) / determinant, d = (yY * xx - xY * xy) / determinant;
  return {
    matrix: [a, b, c, d],
    rotationDegrees: Math.atan2(c - b, a + d) * 180 / Math.PI,
    centroidShift: [nextX - x, nextY - y],
    rmsDisplacement: Math.sqrt(squaredDisplacement / before.length),
  };
}

function geometry(stage) {
  return stage.internal.getDrawableIDs().flatMap((_, index) => Array.from(stage.internal.getDrawableVertices(index)));
}

function measureParameter(stage, id, fraction, baseline) {
  const parameter = stage.parameters[id];
  const span = fraction >= 0 ? parameter.maximum - parameter.default : parameter.default - parameter.minimum;
  const value = parameter.default + span * fraction;
  stage.values = { [id]: value };
  stage.internal.update(1000 / 30, 0);
  const result = fitAffineResponse(baseline, geometry(stage));
  return {
    id, fraction, value, ...result,
    rmsFractionOfCanvasHeight: result.rmsDisplacement / stage.internal.height,
    centroidShiftFractionOfCanvasHeight: result.centroidShift.map(v => v / stage.internal.height),
  };
}

/** Measure rig response, not animation quality. No parameter names are assumed here. */
export function probeRigParameters(stage, parameterIds, fractions = [-0.5, -0.25, 0.25, 0.5]) {
  const physics = stage.internal.physics, values = stage.values;
  stage.internal.physics = undefined;
  const update = pose => { stage.values = pose; stage.internal.update(1000 / 30, 0); };
  try {
    update({});
    const baseline = geometry(stage);
    const supported = parameterIds.filter(id => stage.parameters[id]);
    const probes = supported.flatMap(id => fractions.map(fraction => ({ id, fraction })));
    return probes.map(({ id, fraction }) => measureParameter(stage, id, fraction, baseline));
  } finally {
    stage.values = values;
    stage.internal.physics = physics;
  }
}
