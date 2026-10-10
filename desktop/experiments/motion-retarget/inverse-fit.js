const clamp = value => Math.max(-1, Math.min(1, value));

/** Small pivoted dense solve for the six-control normal equation. */
export function solveLinear(matrix, vector) {
  const rows = matrix.map((row, i) => [...row, vector[i]]), size = vector.length;
  for (let column = 0; column < size; column++) {
    let pivot = column;
    for (let i = column + 1; i < size; i++) if (Math.abs(rows[i][column]) > Math.abs(rows[pivot][column])) pivot = i;
    if (Math.abs(rows[pivot][column]) < 1e-12) return null;
    [rows[column], rows[pivot]] = [rows[pivot], rows[column]];
    const divisor = rows[column][column];
    for (let j = column; j <= size; j++) rows[column][j] /= divisor;
    for (let i = 0; i < size; i++) {
      if (i === column) continue;
      const factor = rows[i][column];
      for (let j = column; j <= size; j++) rows[i][j] -= factor * rows[column][j];
    }
  }
  return rows.map(row => row[size]);
}

function cost(features, controls, { desired, weights, previous, regularization, temporalWeight }) {
  return features.reduce((sum, value, i) => sum + weights[i] * (value - desired[i]) ** 2, 0)
    + controls.reduce((sum, value, i) => sum + regularization * value ** 2 + temporalWeight * (value - previous[i]) ** 2, 0);
}

function jacobian(evaluate, controls, dimension) {
  return controls.map((value, index) => {
    const low = [...controls], high = [...controls];
    low[index] = clamp(value - 0.01); high[index] = clamp(value + 0.01);
    const a = evaluate(low), b = evaluate(high), span = high[index] - low[index];
    return Array.from({ length: dimension }, (_, i) => (b[i] - a[i]) / span);
  });
}

function normalStep({ columns, residual, weights, controls, previous, options, damping }) {
  const size = controls.length;
  const matrix = Array.from({ length: size }, () => Array(size).fill(0));
  const rhs = Array(size).fill(0);
  for (let i = 0; i < size; i++) {
    for (let j = 0; j < size; j++) matrix[i][j] = residual.reduce((sum, _, k) => sum + weights[k] * columns[i][k] * columns[j][k], 0);
    matrix[i][i] += options.regularization + options.temporalWeight + damping;
    rhs[i] = residual.reduce((sum, value, k) => sum + weights[k] * columns[i][k] * value, 0)
      - options.regularization * controls[i] - options.temporalWeight * (controls[i] - previous[i]);
  }
  return solveLinear(matrix, rhs);
}

/** Bounded finite-difference inverse rig. It minimizes measurements, not visual taste. */
export function fitControls(evaluate, desired, initial, weights, settings = {}) {
  const options = { iterations: 8, regularization: 0.00005, temporalWeight: 0.0001, ...settings };
  const previous = settings.previous ?? initial;
  let controls = initial.map(clamp), features = evaluate(controls), damping = 0.0001;
  const objective = (f, q) => cost(f, q, { desired, weights, previous, ...options });
  let value = objective(features, controls), iterations = 0;
  for (; iterations < options.iterations; iterations++) {
    const columns = jacobian(evaluate, controls, desired.length);
    const step = normalStep({ columns, residual: desired.map((v, i) => v - features[i]), weights, controls, previous, options, damping });
    if (!step) break;
    const largest = Math.max(...step.map(Math.abs));
    if (largest < 0.00001) break;
    const scale = Math.min(1, 0.25 / largest);
    const next = controls.map((v, i) => clamp(v + step[i] * scale));
    const nextFeatures = evaluate(next), nextValue = objective(nextFeatures, next);
    if (nextValue < value) {
      controls = next; features = nextFeatures;
      const converged = value - nextValue < 1e-10;
      value = nextValue; damping = Math.max(damping / 3, 1e-7);
      if (converged) break;
    } else damping *= 10;
  }
  return { controls, features, objective: value, iterations,
    saturated: controls.map((value, index) => Math.abs(value) > 0.999 ? index : -1).filter(index => index >= 0) };
}
